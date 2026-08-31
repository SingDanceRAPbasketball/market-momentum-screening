"""Install and manage the local report server as a macOS LaunchAgent."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


SERVICE_LABEL = "io.github.singdancerapbasketball.market-momentum-screening"


def launch_agent_path(home_dir: Optional[Path] = None) -> Path:
    home = (home_dir or Path.home()).resolve()
    return home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"


def build_launch_agent(
    project_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> Dict[str, Any]:
    project = project_dir.resolve()
    executable = project / ".venv" / "bin" / "market-momentum"
    if not executable.is_file():
        raise FileNotFoundError(f"market-momentum executable does not exist: {executable}")
    runtime_dir = project / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    path_entries = [
        str(executable.parent),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    for entry in os.environ.get("PATH", "").split(":"):
        if entry and entry not in path_entries:
            path_entries.append(entry)

    return {
        "Label": SERVICE_LABEL,
        "ProgramArguments": [
            str(executable),
            "serve",
            "--project-dir",
            str(project),
            "--host",
            host,
            "--port",
            str(port),
        ],
        "WorkingDirectory": str(project),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 2,
        "EnvironmentVariables": {
            "PATH": ":".join(path_entries),
            "PYTHONUNBUFFERED": "1",
            "MARKET_MOMENTUM_MANAGED": "launchd",
        },
        "StandardOutPath": str(runtime_dir / "server.stdout.log"),
        "StandardErrorPath": str(runtime_dir / "server.stderr.log"),
    }


def _launchctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["/bin/launchctl", *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "launchctl failed"
        raise RuntimeError(detail)
    return process


def install_service(
    project_dir: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    home_dir: Optional[Path] = None,
) -> Path:
    plist_path = launch_agent_path(home_dir)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    payload = plistlib.dumps(build_launch_agent(project_dir, host=host, port=port), sort_keys=False)
    temporary_path = plist_path.with_suffix(".plist.tmp")
    temporary_path.write_bytes(payload)
    temporary_path.chmod(0o644)
    temporary_path.replace(plist_path)

    domain = f"gui/{os.getuid()}"
    service_target = f"{domain}/{SERVICE_LABEL}"
    _launchctl("bootout", service_target, check=False)
    _launchctl("bootstrap", domain, str(plist_path))
    _launchctl("kickstart", "-k", service_target)
    return plist_path


def service_status(home_dir: Optional[Path] = None) -> Dict[str, Any]:
    domain = f"gui/{os.getuid()}"
    process = _launchctl("print", f"{domain}/{SERVICE_LABEL}", check=False)
    return {
        "label": SERVICE_LABEL,
        "loaded": process.returncode == 0,
        "plist": str(launch_agent_path(home_dir)),
    }


def uninstall_service(home_dir: Optional[Path] = None) -> Path:
    plist_path = launch_agent_path(home_dir)
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{SERVICE_LABEL}", check=False)
    if plist_path.exists():
        plist_path.unlink()
    return plist_path
