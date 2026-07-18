from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from scripts.lib.settings import ROOT, Settings


def _inventory(settings: Settings) -> dict[str, Any]:
    host: dict[str, Any] = {
        "ansible_host": settings.get("DEPLOY_HOST"),
        "ansible_user": settings.get("DEPLOY_USER"),
        "ansible_port": int(settings.get("DEPLOY_PORT", "22")),
        "ansible_python_interpreter": "/usr/bin/python3",
    }
    key = settings.get("DEPLOY_SSH_KEY")
    if key:
        host["ansible_ssh_private_key_file"] = str(Path(key).expanduser())
    return {"all": {"hosts": {"vpn_dispatcher_target": host}}}


def run_playbook(
    settings: Settings,
    playbook: str,
    *,
    extra_vars: dict[str, Any] | None = None,
    check: bool = False,
) -> int:
    settings.validate(deployment=True)
    env = os.environ.copy()
    env.update(settings.public_values())
    env["PAVEL_WORK_RUNTIME_ENV"] = settings.runtime_env()
    env["PAVEL_WORK_SECRETS_JSON"] = json.dumps(settings.secrets())
    env["ANSIBLE_CONFIG"] = str(ROOT / "ansible.cfg")

    with tempfile.TemporaryDirectory(prefix="vpn-dispatcher-") as tmp:
        tmp_path = Path(tmp)
        inventory = tmp_path / "inventory.yml"
        inventory.write_text(
            yaml.safe_dump(_inventory(settings), sort_keys=False),
            encoding="utf-8",
        )
        os.chmod(inventory, 0o600)

        command = [
            "ansible-playbook",
            "-i",
            str(inventory),
            str(ROOT / "ansible" / playbook),
        ]
        if settings.enabled("DEPLOY_SUDO"):
            command.append("--become")
        if check:
            command.append("--check")
        if extra_vars:
            vars_file = tmp_path / "extra-vars.json"
            vars_file.write_text(json.dumps(extra_vars), encoding="utf-8")
            os.chmod(vars_file, 0o600)
            command.extend(["--extra-vars", f"@{vars_file}"])
        try:
            return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode
        except FileNotFoundError:
            raise SystemExit(
                "ansible-playbook is not installed. Run: poetry install"
            ) from None


def run_operate(
    settings: Settings,
    operation: str,
    *,
    check: bool = False,
) -> int:
    return run_playbook(
        settings,
        "operate.yml",
        extra_vars={"operation": operation},
        check=check,
    )
