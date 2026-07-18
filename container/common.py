from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import struct
import subprocess
import time
import urllib.parse
from pathlib import Path


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def require_env(name: str) -> str:
    value = env(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def enabled(name: str, default: bool = False) -> bool:
    value = env(name, "true" if default else "false").casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be true or false")


def secret_file(name: str) -> Path:
    return Path("/run/vpn-dispatcher-secrets") / name


def read_secret(name: str) -> str:
    path = secret_file(name)
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def write_private(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.strip() + "\n", encoding="utf-8")
    path.chmod(0o600)


def run(
    command: list[str],
    *,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"{' '.join(command)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result


def parse_totp_uri(uri: str) -> dict[str, str]:
    """Accept otpauth://totp/...?secret=... or a raw Base32 secret."""
    value = uri.strip()
    if not value:
        raise ValueError("TOTP secret is empty")

    if "://" not in value:
        secret = value.replace(" ", "").replace("-", "").upper()
        if not re.fullmatch(r"[A-Z2-7]+=*", secret):
            raise ValueError(
                "Expected otpauth://totp/... URI or a Base32 TOTP secret"
            )
        return {
            "secret": secret,
            "algorithm": "SHA1",
            "digits": "6",
            "period": "30",
        }

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "otpauth" or parsed.netloc.casefold() != "totp":
        raise ValueError("Expected an otpauth://totp/... URI")
    query = {key.casefold(): value for key, value in urllib.parse.parse_qsl(parsed.query)}
    secret = query.get("secret", "").replace(" ", "").upper()
    if not secret:
        raise ValueError("TOTP URI has no secret")
    return {
        "secret": secret,
        "algorithm": query.get("algorithm", "SHA1").upper(),
        "digits": query.get("digits", "6"),
        "period": query.get("period", "30"),
    }


def totp(uri: str, at: int | None = None) -> str:
    values = parse_totp_uri(uri)
    algorithm = values["algorithm"].lower()
    if algorithm not in {"sha1", "sha256", "sha512"}:
        raise ValueError(f"Unsupported TOTP algorithm: {algorithm}")
    digits = int(values["digits"])
    period = int(values["period"])
    counter = int(at if at is not None else time.time()) // period
    padding = "=" * (-len(values["secret"]) % 8)
    key = base64.b32decode(values["secret"] + padding, casefold=True)
    digest = hmac.new(
        key,
        struct.pack(">Q", counter),
        getattr(hashlib, algorithm),
    ).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10**digits)).zfill(digits)
