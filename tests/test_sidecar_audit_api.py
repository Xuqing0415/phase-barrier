"""v0.20.0 sidecar 审计查询 API 与端到端审计链测试。

覆盖：
- ``GateSidecar.audit`` / ``GateClient.audit`` / ``GET /api/audit``（limit、event 过滤、非法参数）
- 端到端审计链：HTTP 写拒绝 -> 本地 audit.log -> /api/audit 查询 -> 远端 SIEM 推送
- CLI ``python -m anti_shortcut sidecar`` 启动 / 健康检查 / 退出
"""
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anti_shortcut.proxy import WriteDenied
from anti_shortcut.proxy_client import GateClient, GateDenied
from anti_shortcut.sidecar import GateSidecar, make_handler
from anti_shortcut.__main__ import main
from conftest import USER_REQUEST


def _make_sidecar(ws, config=None):
    return GateSidecar(ws, config=config, user_request=USER_REQUEST)


def _serve(sidecar):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(sidecar))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    return server, base


def _get(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _post(base, path, payload):
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


def _stop(server):
    server.shutdown()
    server.server_close()


class _Collector:
    def __init__(self):
        self.bodies = []
        self._lock = threading.Lock()

    def add(self, body):
        with self._lock:
            self.bodies.append(body)

    def events(self):
        out = []
        for body in self.bodies:
            if isinstance(body, list):
                out.extend(body)
            else:
                out.append(body)
        return out


@pytest.fixture
def collector_server():
    collector = _Collector()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            collector.add(json.loads(body.decode("utf-8")))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = "http://127.0.0.1:%d/audit" % server.server_address[1]
    try:
        yield collector, url
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


# ---------- GateSidecar.audit 单元 ----------


def test_audit_empty(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    result = sidecar.audit()
    assert result["ok"] is True
    # 全新工作区只有初始化事件（skill_initialized / gate_dir_policy），无代理事件
    assert all(e["event"] != "proxy_write_ok" and e["event"] != "proxy_write_denied" for e in result["events"])


def test_audit_returns_events_newest_first(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    sidecar.write_file("spec.md", "# spec")  # 阶段 0 允许写 other -> proxy_write_ok
    with pytest.raises(WriteDenied):
        sidecar.write_file("fib.py", "def fib(): pass")  # 阶段 0 拒绝写 source
    result = sidecar.audit()
    assert result["count"] >= 2
    names = [e["event"] for e in result["events"]]
    assert names[0] == "proxy_write_denied"  # 最新在前
    assert "proxy_write_ok" in names
    newest = result["events"][0]
    assert "current_stage" in newest and "stage_name" in newest


def test_audit_limit(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    for i in range(3):
        sidecar.write_file(f"notes{i}.md", f"# note {i}")
    result = sidecar.audit(limit=1)
    assert result["count"] == 1
    assert result["events"][0]["path"].endswith("notes2.md")


def test_audit_event_filter(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    sidecar.write_file("spec.md", "# spec")
    with pytest.raises(WriteDenied):
        sidecar.write_file("fib.py", "x")
    result = sidecar.audit(event="proxy_write_denied")
    assert result["count"] >= 1
    assert all(e["event"] == "proxy_write_denied" for e in result["events"])


def test_audit_skips_malformed_lines(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    sidecar.write_file("spec.md", "# spec")
    log_file = ws / ".agent_gate" / "audit.log"
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write("not-json\n")
        fh.write('{"event": "manual_event"}\n')
    result = sidecar.audit()
    names = [e["event"] for e in result["events"]]
    assert "proxy_write_ok" in names
    assert "manual_event" in names


# ---------- HTTP /api/audit ----------


def test_http_audit_returns_events_and_filter(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    server, base = _serve(sidecar)
    try:
        code, _ = _post(base, "/api/write", {"path": "fib.py", "content": "x"})
        assert code == 403
        code, body = _get(base, "/api/audit")
        assert code == 200 and body["ok"] is True
        assert any(e["event"] == "proxy_write_denied" for e in body["events"])
        code, body = _get(base, "/api/audit?event=proxy_write_denied")
        assert code == 200
        assert body["count"] >= 1
        assert all(e["event"] == "proxy_write_denied" for e in body["events"])
    finally:
        _stop(server)
        sidecar.skill.close()


def test_http_audit_limit_validation(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    server, base = _serve(sidecar)
    try:
        for bad in ("abc", "0", "999", "1.5", "-3"):
            code, body = _get(base, "/api/audit?limit=" + bad)
            assert code == 400, bad
            assert "limit" in body.get("error", "")
        code, body = _get(base, "/api/audit?limit=2")
        assert code == 200
        assert len(body["events"]) <= 2
        code, _ = _get(base, "/api/auditx")
        assert code == 404
    finally:
        _stop(server)
        sidecar.skill.close()


def test_gate_client_audit(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(ws)
    server, base = _serve(sidecar)
    client = GateClient(base)
    try:
        _post(base, "/api/write", {"path": "spec.md", "content": "# s"})
        with pytest.raises(GateDenied):
            client.write_file("fib.py", "x")
        result = client.audit()
        assert result["ok"] is True and result["count"] >= 2
        result = client.audit(limit=1)
        assert len(result["events"]) == 1
        result = client.audit(event="proxy_write_denied")
        assert all(e["event"] == "proxy_write_denied" for e in result["events"])
    finally:
        _stop(server)
        sidecar.skill.close()


# ---------- 端到端审计链 ----------


def test_e2e_denied_write_audit_chain_to_siem(tmp_path, collector_server):
    collector, url = collector_server
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _make_sidecar(
        ws, config={"audit_remote_url": url, "audit_remote_flush_interval": 60}
    )
    server, base = _serve(sidecar)
    try:
        code, body = _post(base, "/api/write", {"path": "fib.py", "content": "x"})
        assert code == 403
        _, audited = _get(base, "/api/audit?event=proxy_write_denied")
        assert audited["count"] >= 1
        assert "实现代码" in audited["events"][0].get("reason", "")
    finally:
        _stop(server)
        sidecar.skill.close()  # 触发远端 sink flush
    events = collector.events()
    assert any(e.get("event") == "proxy_write_denied" for e in events)


# ---------- CLI sidecar 子命令 ----------


def test_cli_sidecar_help(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["sidecar", "--help"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "sidecar" in out and "--port" in out and "--grpc-port" in out


def test_cli_sidecar_serves_and_terminates(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # v0.38.1: stdout 重定向到日志文件（避免 PIPE 阻塞），并放宽就绪超时（macOS 慢启动）
    log_path = tmp_path / "sidecar.log"
    log_fh = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "anti_shortcut",
            "sidecar",
            "--workspace",
            str(ws),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        base = "http://127.0.0.1:%d" % port
        deadline = time.time() + 45
        ok = False
        while time.time() < deadline:
            if proc.poll() is not None:
                log_fh.flush()
                out = log_path.read_text(encoding="utf-8", errors="replace")
                pytest.fail("sidecar exited early: %s" % out[-500:])
            try:
                with urllib.request.urlopen(base + "/healthz", timeout=2) as resp:
                    if resp.status == 200:
                        ok = True
                        break
            except Exception:
                time.sleep(0.2)
        if not ok:
            log_fh.flush()
            out = log_path.read_text(encoding="utf-8", errors="replace")
            pytest.fail("sidecar /healthz not ready in time; log tail:\n%s" % out[-2000:])
        code, body = _get(base, "/api/state")
        assert code == 200 and "current_stage" in body
    finally:
        log_fh.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
