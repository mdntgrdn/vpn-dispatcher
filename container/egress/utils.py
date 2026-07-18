from __future__ import annotations

import time
from pathlib import Path

from container.common import read_secret, run, write_private


def delete_link(name: str) -> None:
    run(["ip", "link", "delete", name], check=False)
    Path(f"/var/run/amneziawg/{name}.sock").unlink(missing_ok=True)


def start_awg(name: str) -> None:
    delete_link(name)
    run(["amneziawg-go", name])
    for _ in range(50):
        if run(["ip", "link", "show", "dev", name], check=False).returncode == 0:
            return
        time.sleep(0.1)
    raise RuntimeError(f"amneziawg-go did not create {name}")


def persistent_private_key(secret_name: str, path: Path, tool: str) -> Path:
    configured = read_secret(secret_name)
    if configured:
        write_private(path, configured)
    elif not path.is_file() or not path.read_text(encoding="utf-8").strip():
        write_private(path, run([tool, "genkey"]).stdout)
    return path
