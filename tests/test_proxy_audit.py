"""v0.19.0 代理审计与 cwd 测试：proxy 事件落 audit.log / 远端推送、exec cwd 全链路。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anti_shortcut.proxy import GateProxy, WriteDenied
from anti_shortcut.proxy_client import GateClient
from anti_shortcut.sidecar import GateSidecar, make_handler
from anti_shortcut.skill import AntiShortcutSkill
from conftest import SPEC, USER_REQUEST

CWD_CMD = "python -c \"import os; print(os.path.exists('marker.txt'))\""


class _Collector:
    def __init__(self):
        self.bodies: list = []
        self._lock = threading.Lock()

    def add(self, body: bytes) -> None:
        with self._lock:
            self.bodies.append(json.loads(body.decode("utf-8")))

    def events(self) -> list:
        out = []
        for body in self.bodies:
            out.extend(body) if isinstance(body, list) else out.append(body)
        return out


@pytest.fixture
def collector_server():
    collector = _Collector()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            collector.add(body)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/audit"
    try:
        yield collector, url
    finally:
        server.shutdown()
        server.server_close()


def _audit_events(ws):
    path = ws / ".agent_gate" / "audit.log"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _make_proxy(ws, config=None):
    skill = AntiShortcutSkill(ws, config=config, user_request=USER_REQUEST)
    return skill, GateProxy(skill)


# ---------- 审计事件 ----------


def test_audit_write_ok(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    skill, proxy = _make_proxy(ws)
    try:
        proxy.write_file("spec.md", SPEC)
    finally:
        skill.close()
    events = _audit_events(ws)
    assert any(
        e.get("event") == "proxy_write_ok"
        and e.get("kind") == "other"
        and e.get("current_stage") == 1
        for e in events
    )


def test_audit_write_denied(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    skill, proxy = _make_proxy(ws)
    try:
        with pytest.raises(WriteDenied):
            proxy.write_file("fib.py", "x")
    finally:
        skill.close()
    events = _audit_events(ws)
    denied = [e for e in events if e.get("event") == "proxy_write_denied"]
    assert denied and "实现代码" in denied[0]["reason"]
    assert denied[0]["path"].endswith("fib.py")


def test_audit_exec_ok(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    skill, proxy = _make_proxy(ws)
    try:
        proxy.execute_command('python -c "print(1)"')
    finally:
        skill.close()
    events = _audit_events(ws)
    assert any(
        e.get("event") == "proxy_exec_ok"
        and e.get("exit_code") == 0
        and e.get("recorded_test_run") is False
        for e in events
    )


def test_audit_exec_denied(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    skill, proxy = _make_proxy(ws)
    try:
        with pytest.raises(Exception):
            proxy.execute_command("python -m pytest -q")
    finally:
        skill.close()
    events = _audit_events(ws)
    denied = [e for e in events if e.get("event") == "proxy_exec_denied"]
    assert denied and "实现代码" in denied[0]["reason"]


def test_audit_exec_timeout(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    skill, proxy = _make_proxy(ws)
    try:
        proxy.execute_command('python -c "import time; time.sleep(30)"', timeout=1)
    finally:
        skill.close()
    events = _audit_events(ws)
    assert any(e.get("event") == "proxy_exec_timeout" for e in events)


def test_audit_remote_push_denied(tmp_path, collector_server):
    collector, url = collector_server
    ws = tmp_path / "ws"
    ws.mkdir()
    skill, proxy = _make_proxy(ws, config={"audit_remote_url": url, "audit_remote_flush_interval": 60})
    try:
        with pytest.raises(WriteDenied):
            proxy.write_file("fib.py", "x")
    finally:
        skill.close()  # 冲刷远端队列
    events = collector.events()
    assert any(
        e.get("event") == "proxy_write_denied" and "实现代码" in (e.get("reason") or "")
        for e in events
    )


# ---------- exec cwd ----------


def test_cli_exec_cwd_ok(capsys, tmp_path):
    from anti_shortcut.__main__ import main

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sub").mkdir()
    (ws / "sub" / "marker.txt").write_text("x", encoding="utf-8")
    rc = main(["exec", "--workspace", str(ws), "--command", CWD_CMD, "--cwd", "sub"])
    out = capsys.readouterr().out
    assert rc == 0 and "True" in out


def test_cli_exec_cwd_outside_rejected(capsys, tmp_path):
    from anti_shortcut.__main__ import main

    ws = tmp_path / "ws"
    ws.mkdir()
    rc = main(["exec", "--workspace", str(ws), "--command", "echo hi", "--cwd", "../outside"])
    out = capsys.readouterr().err
    assert rc == 1 and "cwd 越出工作区" in out


def _serve(sidecar):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sidecar))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def _post(base, path, payload):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_http_exec_cwd_ok(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sub").mkdir()
    (ws / "sub" / "marker.txt").write_text("x", encoding="utf-8")
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, payload = _post(base, "/api/exec", {"command": CWD_CMD, "cwd": "sub"})
        assert status == 200 and payload["exit_code"] == 0
        assert "True" in payload["output"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_exec_cwd_invalid_type_400(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, _ = _post(base, "/api/exec", {"command": "echo hi", "cwd": 123})
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()


def test_http_exec_cwd_outside_400(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, payload = _post(base, "/api/exec", {"command": "echo hi", "cwd": "../outside"})
        assert status == 400 and "越出工作区" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_client_exec_cwd_passthrough(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "sub").mkdir()
    (ws / "sub" / "marker.txt").write_text("x", encoding="utf-8")
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        client = GateClient(base)
        result = client.execute_command(CWD_CMD, cwd="sub")
        assert result["exit_code"] == 0 and "True" in result["output"]
    finally:
        server.shutdown()
        server.server_close()