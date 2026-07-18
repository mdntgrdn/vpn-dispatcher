from __future__ import annotations

import base64
import json
import shlex
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts.lib.remote import container_name, link_ready, ssh_base, vpn_kill
from scripts.lib.settings import Settings


def login_url(settings: Settings) -> str:
    host = settings.get("FORTI_HOST")
    port = settings.get("FORTI_PORT", "443")
    realm = settings.get("FORTI_REALM")
    realm_query = f"&realm={realm}" if realm else ""
    return f"https://{host}:{port}/remote/saml/start?redirect=1{realm_query}"


def saml_listener_ready(settings: Settings, saml_port: str) -> bool:
    """Detect openfortivpn SAML listen without connecting (connect can eat the accept)."""
    port_hex = f"{int(saml_port):04X}"
    result = subprocess.run(
        [
            *ssh_base(settings),
            "docker",
            "exec",
            container_name(settings),
            "python3",
            "-c",
            (
                "import pathlib,sys;"
                f"needle=':{port_hex}';"
                "text=pathlib.Path('/proc/net/tcp').read_text();"
                "sys.exit(0 if any(needle in line and line.split()[3]=='0A' "
                "for line in text.splitlines()[1:]) else 1)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode == 0:
        return True
    logs = subprocess.run(
        [
            *ssh_base(settings),
            "docker",
            "logs",
            "--tail",
            "80",
            container_name(settings),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return "Listening for SAML login" in (logs.stdout or "")


def deliver_saml_id(settings: Settings, saml_port: str, saml_id: str) -> tuple[bool, str]:
    """Ask openfortivpn inside the container for /?id=... (no Docker port publish)."""
    payload = json.dumps({"port": int(saml_port), "id": saml_id})
    script = """
import json, socket, sys
d = json.load(sys.stdin)
s = socket.create_connection(("127.0.0.1", int(d["port"])), 10)
path = "/?id=" + d["id"]
req = (
    "GET " + path + " HTTP/1.1\\r\\n"
    "Host: 127.0.0.1:" + str(d["port"]) + "\\r\\n"
    "Connection: close\\r\\n\\r\\n"
).encode()
s.sendall(req)
chunks = []
while True:
    buf = s.recv(65536)
    if not buf:
        break
    chunks.append(buf)
sys.stdout.buffer.write(b"".join(chunks))
"""
    encoded = base64.b64encode(script.encode()).decode()
    pycode = f"import base64; exec(base64.b64decode({encoded!r}).decode())"
    remote = (
        f"docker exec -i {shlex.quote(container_name(settings))} "
        f"python3 -c {shlex.quote(pycode)}"
    )
    result = subprocess.run(
        [*ssh_base(settings), remote],
        input=payload,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    body = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0:
        return True, body[:500]
    return False, body[:500] or f"exit {result.returncode}"


def serve_saml_callback(
    settings: Settings,
    local_port: int,
    saml_port: str,
    delivered: threading.Event,
) -> ThreadingHTTPServer:
    lock = threading.Lock()
    state = {"done": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            saml_id = (params.get("id") or [""])[0].strip()
            if not saml_id:
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<html><body>Missing id= in SAML callback.</body></html>")
                return

            with lock:
                already = state["done"]
                if not already:
                    state["done"] = True

            if already:
                print(f"Ignoring duplicate SAML callback id={saml_id[:24]}...", flush=True)
                self._ok_page("SAML id already delivered. You can close this tab.")
                return

            print("SAML callback id received — delivering inside container...", flush=True)
            ok, detail = deliver_saml_id(settings, saml_port, saml_id)
            if ok:
                print("SAML id delivered to openfortivpn.", flush=True)
                delivered.set()
                self._ok_page("SAML OK — return to the terminal and wait for forti0.")
            else:
                state["done"] = False
                print(f"Failed to deliver SAML id: {detail!r}", file=sys.stderr, flush=True)
                self.send_response(502)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body>Failed to reach openfortivpn. "
                    b"Check forti_reconnect terminal / container logs.</body></html>"
                )

        def _ok_page(self, message: str) -> None:
            body = f"<html><body><h3>{message}</h3></body></html>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", local_port), Handler)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot bind 127.0.0.1:{local_port} ({exc}). "
            "Stop any old ssh -L / forti_reconnect using that port."
        ) from exc
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def login(settings: Settings, *, open_browser: bool, timeout: int) -> int:
    if not settings.enabled("FORTI_ENABLED"):
        print("FORTI_ENABLED is false.", file=sys.stderr)
        return 2

    port = int(settings.get("FORTI_SAML_PORT", "8020"))
    saml_port = str(port)
    url = login_url(settings)
    delivered = threading.Event()

    print("=" * 72, flush=True)
    print("Forti SAML login", flush=True)
    print(f"URL:  {url}", flush=True)
    print(f"Callback: http://127.0.0.1:{port}/", flush=True)
    print("KEEP THIS TERMINAL OPEN until forti0 is up.", flush=True)
    print("=" * 72, flush=True)

    try:
        server = serve_saml_callback(settings, port, saml_port, delivered)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(f"Listening on 127.0.0.1:{port}", flush=True)
    print("Waiting for openfortivpn SAML listener inside the container...", flush=True)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if saml_listener_ready(settings, saml_port):
            break
        time.sleep(2)
    else:
        print(
            "openfortivpn is not listening for SAML yet. Check container logs.",
            file=sys.stderr,
        )
        server.shutdown()
        return 2

    print("openfortivpn is ready. Complete SSO in the browser.", flush=True)
    if open_browser:
        webbrowser.open(url)

    try:
        while time.monotonic() < deadline:
            if link_ready(settings, "forti0"):
                print("forti0 is up — SAML login succeeded.", flush=True)
                return 0
            time.sleep(2)
        print(f"Timed out after {timeout}s waiting for forti0.", file=sys.stderr)
        if not delivered.is_set():
            print("No SAML callback with id= was delivered.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nAborted.", flush=True)
        return 130
    finally:
        server.shutdown()


def reconnect(settings: Settings, *, open_browser: bool, timeout: int) -> int:
    if not settings.enabled("FORTI_ENABLED"):
        print("FORTI_ENABLED is false.", file=sys.stderr)
        return 2
    killed = vpn_kill(settings, "openfortivpn")
    if killed != 0:
        return killed
    print(
        "Container stays up; Forti restart loop will respawn openfortivpn "
        "(up to 2 attempts, ~30s apart), then SAML is required again.",
        flush=True,
    )
    return login(settings, open_browser=open_browser, timeout=timeout)
