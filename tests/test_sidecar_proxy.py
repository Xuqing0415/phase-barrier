"""v0.17.0 透明代理测试：GateProxy / sidecar /api/write+/api/exec / GateClient。"""
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from anti_shortcut.proxy import ExecDenied, GateProxy, ProxyError, WriteDenied
from anti_shortcut.proxy_client import GateClient, GateClientError, GateDenied
from anti_shortcut.sidecar import GateSidecar, make_handler
from anti_shortcut.skill import AntiShortcutSkill
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC, USER_REQUEST


def _make_skill(ws):
    return AntiShortcutSkill(ws, user_request=USER_REQUEST)


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


# ---------- GateProxy 单元：路径解析 ----------


def test_proxy_resolve_relative_and_absolute(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    assert proxy.resolve_write_path("fib.py") == (ws / "fib.py").resolve()
    assert proxy.resolve_write_path(ws / "sub" / "a.py") == (ws / "sub" / "a.py").resolve()


def test_proxy_resolve_traversal_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ProxyError):
        proxy.resolve_write_path("../evil.txt")
    with pytest.raises(ProxyError):
        proxy.resolve_write_path("../../etc/passwd")


def test_proxy_resolve_absolute_outside_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ProxyError):
        proxy.resolve_write_path(outside / "x.txt")
    with pytest.raises(ProxyError):
        proxy.resolve_write_path(str(outside / "x.txt"))


def test_proxy_resolve_empty_or_bad_type_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ProxyError):
        proxy.resolve_write_path("")
    with pytest.raises(ProxyError):
        proxy.resolve_write_path("   ")
    with pytest.raises(ProxyError):
        proxy.resolve_write_path(123)  # type: ignore[arg-type]


def test_proxy_resolve_workspace_root_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ProxyError):
        proxy.resolve_write_path(".")


# ---------- GateProxy 单元：写入门禁 ----------


def test_proxy_write_source_denied_before_tests(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(WriteDenied) as exc_info:
        proxy.write_file("fib.py", "def fib(n): return n")
    assert "测试用例" in str(exc_info.value)


def test_proxy_write_test_allowed_at_spec_stage(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    result = proxy.write_file("test_fib.py", GOOD_TESTS)
    assert result["ok"] and result["kind"] == "test"


def test_proxy_write_gate_dir_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(WriteDenied):
        proxy.write_file(".agent_gate/state.json", "{}")
    with pytest.raises(WriteDenied):
        proxy.write_file(str(ws / ".agent_gate" / "x"), "x")


def test_proxy_write_dir_target_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "adir").mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ProxyError):
        proxy.write_file("adir", "x")


def test_proxy_write_non_str_content_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ProxyError):
        proxy.write_file("notes.txt", 123)  # type: ignore[arg-type]


def test_proxy_write_ok_and_records_change(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    skill = _make_skill(ws)
    proxy = GateProxy(skill)
    proxy.write_file("spec.md", SPEC)
    assert skill.advance_stage(2)["success"]
    proxy.write_file("test_fib.py", GOOD_TESTS)
    assert skill.advance_stage(3)["success"]
    result = proxy.write_file("fib.py", GOOD_IMPL)
    assert result["ok"] and result["kind"] == "source"
    assert (ws / "fib.py").read_text(encoding="utf-8") == GOOD_IMPL
    assert skill.state.get_evidence("last_source_change_path") is not None


# ---------- GateProxy 单元：命令执行门禁 ----------


def test_proxy_exec_test_command_denied_before_impl(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ExecDenied) as exc_info:
        proxy.execute_command("python -m pytest -q")
    assert "实现代码" in str(exc_info.value)


def test_proxy_exec_ok_returns_output(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    result = proxy.execute_command('python -c "print(42)"')
    assert result["ok"] and result["exit_code"] == 0
    assert "42" in result["output"]
    assert result["recorded_test_run"] is False


def test_proxy_exec_nonzero_exit_returns_code(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    result = proxy.execute_command('python -c "import sys; sys.exit(3)"')
    assert result["ok"] and result["exit_code"] == 3


def test_proxy_exec_timeout(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    result = proxy.execute_command('python -c "import time; time.sleep(30)"', timeout=1)
    assert result["timed_out"] is True and result["exit_code"] == -1


def test_proxy_exec_invalid_timeout_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ProxyError):
        proxy.execute_command("echo hi", timeout=0)
    with pytest.raises(ProxyError):
        proxy.execute_command("echo hi", timeout=99999)


def test_proxy_exec_cwd_outside_rejected(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    proxy = GateProxy(_make_skill(ws))
    with pytest.raises(ProxyError):
        proxy.execute_command("echo hi", cwd=str(tmp_path / "outside"))


def test_proxy_exec_records_test_run(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    skill = _make_skill(ws)
    proxy = GateProxy(skill)
    proxy.write_file("spec.md", SPEC)
    skill.advance_stage(2)
    proxy.write_file("test_fib.py", GOOD_TESTS)
    skill.advance_stage(3)
    proxy.write_file("fib.py", GOOD_IMPL)
    skill.advance_stage(4)
    result = proxy.execute_command("python -m pytest test_fib.py -q", timeout=60)
    assert result["recorded_test_run"] is True
    record = skill.state.get_evidence("last_test_run")
    assert record is not None and record.get("passed") is True


# ---------- HTTP 端点 ----------


def test_http_write_denied_403(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, payload = _post(base, "/api/write", {"path": "fib.py", "content": "x"})
        assert status == 403 and payload["ok"] is False
    finally:
        _stop(server)


def test_http_write_ok_200(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, payload = _post(base, "/api/write", {"path": "spec.md", "content": SPEC})
        assert status == 200 and payload["ok"]
        assert (ws / "spec.md").read_text(encoding="utf-8") == SPEC
    finally:
        _stop(server)


def test_http_write_invalid_payload_400(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, _ = _post(base, "/api/write", {"path": "", "content": "x"})
        assert status == 400
        status, _ = _post(base, "/api/write", {"content": "x"})
        assert status == 400
        status, _ = _post(base, "/api/write", {"path": "a.txt", "content": 123})
        assert status == 400
        status, _ = _post(base, "/api/write", {"path": "../evil.txt", "content": "x"})
        assert status == 400
        status, _ = _post(base, "/api/write", {"path": ".agent_gate/state.json", "content": "{}"})
        assert status == 403
    finally:
        _stop(server)


def test_http_exec_denied_403(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, payload = _post(base, "/api/exec", {"command": "python -m pytest -q"})
        assert status == 403 and payload["ok"] is False
    finally:
        _stop(server)


def test_http_exec_ok_200(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, payload = _post(base, "/api/exec", {"command": 'python -c "print(7)"'})
        assert status == 200 and payload["exit_code"] == 0
        assert "7" in payload["output"]
    finally:
        _stop(server)


def test_http_exec_invalid_payload_400(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, _ = _post(base, "/api/exec", {"command": "  "})
        assert status == 400
        status, _ = _post(base, "/api/exec", {"command": "echo hi", "timeout": "x"})
        assert status == 400
        status, _ = _post(base, "/api/exec", {"command": "echo hi", "timeout": 0})
        assert status == 400
        status, _ = _post(base, "/api/exec", {"command": "echo hi", "timeout": True})
        assert status == 400
    finally:
        _stop(server)


def test_http_exec_timeout_returns_timed_out(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        status, payload = _post(
            base, "/api/exec", {"command": 'python -c "import time; time.sleep(30)"', "timeout": 1}
        )
        assert status == 200 and payload["timed_out"] is True
    finally:
        _stop(server)


# ---------- GateClient ----------


def test_client_write_denied_raises_gate_denied(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        client = GateClient(base)
        with pytest.raises(GateDenied) as exc_info:
            client.write_file("fib.py", "x")
        assert "测试用例" in str(exc_info.value)
        with pytest.raises(GateDenied):
            client.execute_command("python -m pytest -q")
    finally:
        _stop(server)


def test_client_full_flow_to_delivery(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        client = GateClient(base)
        assert client.state()["current_stage"] == 1
        client.write_file("spec.md", SPEC)
        assert client.advance(2)["stage"] == 2
        client.write_file("test_fib.py", GOOD_TESTS)
        assert client.advance(3)["stage"] == 3
        client.write_file("fib.py", GOOD_IMPL)
        assert client.advance(4)["stage"] == 4
        result = client.execute_command("python -m pytest test_fib.py -q", timeout=60)
        assert result["recorded_test_run"] is True
        payload = client.advance(5)
        assert payload["stage"] == 6
        assert client.state()["is_complete"] is True
    finally:
        _stop(server)


def test_client_connection_refused_raises(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    client = GateClient("http://127.0.0.1:%d" % port, timeout=2)
    with pytest.raises(GateClientError):
        client.state()


def test_client_http_error_raises_gate_client_error(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        client = GateClient(base)
        with pytest.raises(GateClientError):
            client._request("GET", "/nope")
    finally:
        _stop(server)


def test_client_record_helpers(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request=USER_REQUEST))
    try:
        client = GateClient(base)
        payload = client.record_test_run(0, "3 passed")
        assert payload["ok"] and payload["record"]["passed"] is True
        payload = client.record_source_change("fib.py")
        assert payload["ok"] and payload["path"] == "fib.py"
    finally:
        _stop(server)