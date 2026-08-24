import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from market_momentum.server import AuthController, RefreshController, make_handler


def read_json(url: str):
    with urlopen(url, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def test_local_refresh_server_requires_token_and_reports_completion(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "latest.html").write_text("<h1>test</h1>", encoding="utf-8")
    controller = RefreshController(tmp_path, command=["/usr/bin/true"])
    auth = AuthController(
        login_command=[
            sys.executable,
            "-c",
            "import json,sys; key=sys.stdin.read().strip(); print(json.dumps({'ok': key == 'sk-test-123', 'data': {'configured': True}}))",
        ],
        status_command=[
            sys.executable,
            "-c",
            "import json; print(json.dumps({'ok': True, 'data': {'configured': True, 'source': 'keyring'}}))",
        ],
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(output, controller, auth),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        health = read_json(f"{base_url}/api/health")
        assert health["ok"]
        assert health["refresh_available"]
        assert health["auth_available"]

        auth_status = read_json(f"{base_url}/api/auth/status")
        assert auth_status["configured"]
        assert auth_status["source"] == "keyring"

        auth_request = Request(
            f"{base_url}/api/auth/login",
            data=json.dumps({"api_key": "sk-test-123"}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Refresh-Token": health["refresh_token"],
            },
        )
        with urlopen(auth_request, timeout=3) as response:
            auth_result_text = response.read().decode("utf-8")
            auth_result = json.loads(auth_result_text)
        assert auth_result["ok"]
        assert auth_result["configured"]
        assert "sk-test-123" not in auth_result_text

        try:
            urlopen(Request(f"{base_url}/api/refresh", method="POST"), timeout=3)
            raise AssertionError("refresh without a token should fail")
        except HTTPError as error:
            assert error.code == 403

        request = Request(
            f"{base_url}/api/refresh",
            method="POST",
            headers={"X-Refresh-Token": health["refresh_token"]},
        )
        with urlopen(request, timeout=3) as response:
            started = json.loads(response.read().decode("utf-8"))
        assert response.status == 202
        assert started["status"]["phase"] == "running"

        status = None
        for _ in range(30):
            status = read_json(f"{base_url}/api/refresh/status")["status"]
            if status["phase"] != "running":
                break
            time.sleep(0.02)
        assert status["phase"] == "success"
        assert status["return_code"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
