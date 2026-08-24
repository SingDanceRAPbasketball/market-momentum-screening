"""Local-only HTTP server with a guarded report refresh endpoint."""

from __future__ import annotations

import ipaddress
import json
import secrets
import shutil
import subprocess
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple


class RefreshController:
    """Run at most one refresh process and expose a small status snapshot."""

    def __init__(self, project_dir: Path, command: Optional[Sequence[str]] = None) -> None:
        self.project_dir = project_dir.resolve()
        self.command = list(command or ["/bin/zsh", str(self.project_dir / "scripts" / "refresh_real_reports.zsh")])
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {
            "phase": "idle",
            "message": "尚未刷新",
            "started_at": None,
            "finished_at": None,
            "return_code": None,
        }

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(self) -> Tuple[bool, Dict[str, Any]]:
        with self._lock:
            if self._state["phase"] == "running":
                return False, dict(self._state)
            self._state = {
                "phase": "running",
                "message": "正在同步同花顺数据并重建两张报告",
                "started_at": datetime.now().astimezone().isoformat(),
                "finished_at": None,
                "return_code": None,
            }
        thread = threading.Thread(target=self._run, name="market-report-refresh", daemon=True)
        thread.start()
        return True, self.snapshot()

    def _run(self) -> None:
        log_path = self.project_dir / "runtime" / "refresh.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.run(
                    self.command,
                    cwd=str(self.project_dir),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    text=True,
                )
            succeeded = process.returncode == 0
            with self._lock:
                self._state.update(
                    {
                        "phase": "success" if succeeded else "error",
                        "message": (
                            "刷新完成，页面即将重新载入"
                            if succeeded
                            else "刷新失败，请查看 runtime/refresh.log"
                        ),
                        "finished_at": datetime.now().astimezone().isoformat(),
                        "return_code": process.returncode,
                    }
                )
        except Exception:
            with self._lock:
                self._state.update(
                    {
                        "phase": "error",
                        "message": "刷新服务异常，请查看本地终端",
                        "finished_at": datetime.now().astimezone().isoformat(),
                        "return_code": -1,
                    }
                )


class AuthController:
    """Store a supplied API key through the official CLI without logging it."""

    def __init__(
        self,
        login_command: Optional[Sequence[str]] = None,
        status_command: Optional[Sequence[str]] = None,
    ) -> None:
        self.login_command = list(
            login_command
            or [
                "hithink-finance",
                "auth",
                "login",
                "--api-key-stdin",
                "--replace",
                "--format",
                "json",
            ]
        )
        self.status_command = list(
            status_command
            or ["hithink-finance", "auth", "status", "--format", "json"]
        )
        self._lock = threading.Lock()

    def available(self) -> bool:
        executable = self.login_command[0]
        return Path(executable).is_file() if "/" in executable else shutil.which(executable) is not None

    @staticmethod
    def _safe_error(payload: Dict[str, Any]) -> Dict[str, Any]:
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        return {
            "code": str(error.get("code") or "AUTH_FAILED"),
            "hint": str(error.get("hint") or "请检查 API Key 后重试"),
        }

    def _run_json(
        self,
        command: Sequence[str],
        *,
        input_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            process = subprocess.run(
                list(command),
                input=input_text,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {
                "ok": False,
                "error": {"code": "AUTH_CLI_UNAVAILABLE", "hint": "认证命令不可用，请检查本机 CLI"},
            }
        try:
            payload = json.loads(process.stdout)
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if process.returncode != 0 or payload.get("ok") is not True:
            return {"ok": False, "error": self._safe_error(payload)}
        return payload

    def status(self) -> Dict[str, Any]:
        if not self.available():
            return {"ok": False, "configured": False, "error": "auth_cli_unavailable"}
        payload = self._run_json(self.status_command)
        if not payload.get("ok"):
            return {"ok": False, "configured": False, "error": payload["error"]["code"]}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return {
            "ok": True,
            "configured": bool(data.get("configured")),
            "source": data.get("source"),
            "profile": data.get("profile"),
        }

    def login(self, api_key: str) -> Tuple[int, Dict[str, Any]]:
        key = api_key.strip()
        if not 8 <= len(key) <= 4096 or any(character.isspace() for character in key):
            return 400, {
                "ok": False,
                "error": {"code": "INVALID_API_KEY", "hint": "API Key 格式不正确"},
            }
        if not self.available():
            return 503, {
                "ok": False,
                "error": {"code": "AUTH_CLI_UNAVAILABLE", "hint": "本机未找到认证命令"},
            }
        if not self._lock.acquire(blocking=False):
            return 409, {
                "ok": False,
                "error": {"code": "AUTH_BUSY", "hint": "正在保存另一份凭据，请稍后重试"},
            }
        try:
            payload = self._run_json(self.login_command, input_text=f"{key}\n")
        finally:
            key = ""
            self._lock.release()
        if not payload.get("ok"):
            return 400, {"ok": False, "error": payload["error"]}
        return 200, {"ok": True, "configured": True, "message": "API Key 已保存到系统凭据库"}


def _json_bytes(value: Dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def make_handler(
    output_dir: Path,
    controller: RefreshController,
    auth_controller: Optional[AuthController] = None,
):
    auth = auth_controller or AuthController()

    class ReportHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(output_dir), **kwargs)

        def _is_loopback(self) -> bool:
            try:
                return ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                return False

        def _send_json(self, status: int, value: Dict[str, Any]) -> None:
            body = _json_bytes(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path == "/api/health":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "refresh_available": True,
                        "auth_available": auth.available(),
                        "refresh_token": controller.token,
                        "status": controller.snapshot(),
                    },
                )
                return
            if self.path == "/api/refresh/status":
                self._send_json(200, {"ok": True, "status": controller.snapshot()})
                return
            if self.path == "/api/auth/status":
                if not self._is_loopback():
                    self._send_json(403, {"ok": False, "error": "loopback_only"})
                    return
                status = auth.status()
                self._send_json(200 if status.get("ok") else 503, status)
                return
            super().do_GET()

        def do_POST(self) -> None:
            if self.path not in {"/api/refresh", "/api/auth/login"}:
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            if not self._is_loopback():
                self._send_json(403, {"ok": False, "error": "loopback_only"})
                return
            if self.headers.get("X-Refresh-Token") != controller.token:
                self._send_json(403, {"ok": False, "error": "invalid_refresh_token"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                self._send_json(400, {"ok": False, "error": "invalid_content_length"})
                return
            maximum = 8192 if self.path == "/api/auth/login" else 1024
            if content_length < 0 or content_length > maximum:
                self._send_json(413, {"ok": False, "error": "request_too_large"})
                return
            body = self.rfile.read(content_length) if content_length else b""
            if self.path == "/api/auth/login":
                if "application/json" not in self.headers.get("Content-Type", ""):
                    self._send_json(415, {"ok": False, "error": "json_required"})
                    return
                try:
                    payload = json.loads(body.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(400, {"ok": False, "error": "invalid_json"})
                    return
                api_key = payload.get("api_key") if isinstance(payload, dict) else None
                if not isinstance(api_key, str):
                    self._send_json(400, {"ok": False, "error": "api_key_required"})
                    return
                status_code, result = auth.login(api_key)
                self._send_json(status_code, result)
                return
            started, status = controller.start()
            self._send_json(
                202 if started else 409,
                {"ok": started, "status": status, "error": None if started else "already_running"},
            )

        def end_headers(self) -> None:
            if self.path.endswith(".html") or self.path == "/":
                self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            super().end_headers()

        def log_message(self, format: str, *args) -> None:
            print(f"[{self.log_date_time_string()}] {format % args}")

    return ReportHandler


def serve_reports(project_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    try:
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_loopback:
            raise ValueError("报告刷新服务只允许绑定回环地址")
    except ValueError as error:
        raise ValueError("--host 必须是 IPv4 回环地址，例如 127.0.0.1") from error
    if not 0 <= port <= 65535:
        raise ValueError("--port 必须在 0 到 65535 之间")

    project_dir = project_dir.resolve()
    output_dir = project_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    controller = RefreshController(project_dir)
    auth_controller = AuthController()
    server = ThreadingHTTPServer(
        (host, port),
        make_handler(output_dir, controller, auth_controller),
    )
    actual_port = server.server_address[1]
    print(f"本地报告服务: http://{host}:{actual_port}/latest.html")
    print("页面可安全保存 API Key，并使用系统凭据一键刷新；按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
