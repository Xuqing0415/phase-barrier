"""K8s sidecar HTTP 门禁服务测试（v0.7.0）。"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from anti_shortcut.sidecar import GateSidecar, make_handler
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC


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
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _stop(server):
    server.shutdown()
    server.server_close()


def test_sidecar_healthz_and_404(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request="实现斐波那契函数"))
    try:
        status, payload = _get(base, "/healthz")
        assert status == 200 and payload["status"] == "ok"
        status, _ = _get(base, "/nope")
        assert status == 404
    finally:
        _stop(server)


def test_sidecar_full_flow_via_http(tmp_path):
    """验收：Agent 只通过 HTTP 即可推进完整流程，测试一次通过跳过修复。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request="实现斐波那契函数"))
    try:
        status, state = _get(base, "/api/state")
        assert status == 200 and state["current_stage"] == 1

        # 不能原地踏步 / 跳跃
        status, payload = _post(base, "/api/advance", {"new_stage": 1})
        assert status == 409 and not payload["success"]

        (ws / "spec.md").write_text(SPEC, encoding="utf-8")
        status, payload = _post(base, "/api/advance", {"new_stage": 2})
        assert status == 200 and payload["stage"] == 2

        (ws / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
        status, payload = _post(base, "/api/advance", {"new_stage": 3})
        assert status == 200 and payload["stage"] == 3

        (ws / "fib.py").write_text(GOOD_IMPL, encoding="utf-8")
        status, payload = _post(base, "/api/advance", {"new_stage": 4})
        assert status == 200 and payload["stage"] == 4

        # 上报测试运行（带覆盖率），一次通过 → 跳过修复直接交付
        status, payload = _post(
            base,
            "/api/test-run",
            {"exit_code": 0, "output": "3 passed\ncoverage: 92.5% of statements\n"},
        )
        assert status == 200 and payload["ok"] and payload["record"]["coverage"] == 92.5

        status, payload = _post(base, "/api/advance", {"new_stage": 5})
        assert status == 200 and payload["stage"] == 6
        status, state = _get(base, "/api/state")
        assert state["is_complete"] is True
    finally:
        _stop(server)


def test_sidecar_source_change_forces_retest(tmp_path):
    """测试后改码必须进入阶段 5 回归，重测通过后才能交付。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request="实现斐波那契函数"))
    try:
        (ws / "spec.md").write_text(SPEC, encoding="utf-8")
        _post(base, "/api/advance", {"new_stage": 2})
        (ws / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
        _post(base, "/api/advance", {"new_stage": 3})
        (ws / "fib.py").write_text(GOOD_IMPL, encoding="utf-8")
        _post(base, "/api/advance", {"new_stage": 4})

        _post(base, "/api/test-run", {"exit_code": 0, "output": "3 passed"})
        status, payload = _post(base, "/api/source-change", {"path": "fib.py"})
        assert status == 200 and payload["ok"]

        # 测试后改码 → 必须进入阶段 5 回归
        status, payload = _post(base, "/api/advance", {"new_stage": 5})
        assert status == 200 and payload["stage"] == 5

        # 重测通过 → 交付
        _post(base, "/api/test-run", {"exit_code": 0, "output": "3 passed"})
        status, payload = _post(base, "/api/advance", {"new_stage": 6})
        assert status == 200 and payload["stage"] == 6
        status, state = _get(base, "/api/state")
        assert state["is_complete"] is True
    finally:
        _stop(server)


def test_sidecar_bad_payloads(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    server, base = _serve(GateSidecar(ws, user_request="实现斐波那契函数"))
    try:
        status, payload = _post(base, "/api/advance", {"new_stage": "x"})
        assert status == 400
        status, payload = _post(base, "/api/test-run", {"exit_code": "0"})
        assert status == 400
        status, payload = _post(base, "/api/source-change", {"path": ""})
        assert status == 400
    finally:
        _stop(server)
