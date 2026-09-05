"""K8s sidecar gRPC 门禁服务直调测试（v0.41.1）。

与 tests/test_grpc_service.py 的 in-process RPC 用例互补：本文件在**主线程**直接调用
PhaseBarrierServicer 各 RPC 方法（fake context 记录 abort），保证 CI 覆盖率门禁在任意
线程追踪行为下都能测到 servicer 业务代码 —— gRPC handler 默认跑在 worker 线程，
默认 coverage 配置曾漏测导致 grpc_service.py 仅 17%。
"""
from __future__ import annotations

import json

import pytest

try:
    import grpc  # type: ignore[import-untyped]

    import anti_shortcut.grpc_service as grpc_service_mod
    from anti_shortcut.grpc_service import (
        PhaseBarrierServicer,
        create_grpc_server,
        main,
        serve_grpc,
    )
    from anti_shortcut.proto import sidecar_pb2

    _HAVE_GRPC = True
    _GRPC_IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover - 可选依赖缺失
    if getattr(exc, "name", "") == "grpc":
        grpc = None  # type: ignore[assignment]
        _HAVE_GRPC = False
        _GRPC_IMPORT_ERROR = "grpcio 未安装，跳过 gRPC 直调用例"
    else:
        raise
except Exception as exc:  # pragma: no cover - 其他导入异常直接暴露，避免 CI 静默跳过
    grpc = None  # type: ignore[assignment]
    _HAVE_GRPC = False
    _GRPC_IMPORT_ERROR = "grpc/proto 导入异常: " + repr(exc)

from anti_shortcut.sidecar import GateSidecar
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC

_GRPC_SKIP_REASON = _GRPC_IMPORT_ERROR if isinstance(_GRPC_IMPORT_ERROR, str) else 'grpcio 已安装，不跳过'
pytestmark = pytest.mark.skipif(not _HAVE_GRPC, reason=_GRPC_SKIP_REASON)


class _AbortError(Exception):
    """模拟真实 context.abort 的抛错行为，携带状态码与详情。"""

    def __init__(self, code: object, details: str) -> None:
        super().__init__(details)
        self.code = code
        self.details = details


class _FakeContext:
    """主线程直调的最小 context：abort 记录状态码/详情并抛错。"""

    def __init__(self) -> None:
        self.aborted = None

    def abort(self, code: object, details: str) -> None:
        self.aborted = (code, details)
        raise _AbortError(code, details)


def _call(method, request) -> tuple:
    """直接调用 servicer 方法：成功返回 (reply, None)，被拒返回 (None, exc)。"""
    ctx = _FakeContext()
    try:
        return method(request, ctx), None
    except _AbortError as exc:
        return None, exc


def _servicer(ws) -> PhaseBarrierServicer:
    sidecar = GateSidecar(ws, user_request="实现斐波那契函数")
    return PhaseBarrierServicer(sidecar)


def test_direct_get_state_and_advance_errors(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = _servicer(ws)

    reply, err = _call(svc.GetState, sidecar_pb2.GetStateRequest())
    assert err is None and reply is not None
    assert reply.current_stage == 1 and reply.stage_name and reply.is_complete is False

    # new_stage 越界 -> INVALID_ARGUMENT
    reply, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=99))
    assert reply is None and err is not None
    assert err.code == grpc.StatusCode.INVALID_ARGUMENT

    # 未写 spec 直接推进 -> FAILED_PRECONDITION
    reply, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=2))
    assert reply is None and err is not None
    assert err.code == grpc.StatusCode.FAILED_PRECONDITION


def test_direct_full_flow_advance(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = _servicer(ws)

    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    reply, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=2))
    assert err is None and reply is not None and reply.success and reply.current_stage == 2

    (ws / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
    reply, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=3))
    assert err is None and reply.success and reply.current_stage == 3

    (ws / "fib.py").write_text(GOOD_IMPL, encoding="utf-8")
    reply, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=4))
    assert err is None and reply.success and reply.current_stage == 4

    reply, err = _call(
        svc.RecordTestRun,
        sidecar_pb2.RecordTestRunRequest(exit_code=0, output="3 passed\ncoverage: 92.5% of statements\n"),
    )
    assert err is None and reply is not None
    assert reply.ok and reply.passed and reply.coverage == 92.5

    reply, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=5))
    assert err is None and reply.success

    state, err = _call(svc.GetState, sidecar_pb2.GetStateRequest())
    assert err is None and state.is_complete is True and state.current_stage == 6


def test_direct_write_file_denied_and_ok(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = _servicer(ws)

    # 写入门禁目录 -> PERMISSION_DENIED
    reply, err = _call(
        svc.WriteFile,
        sidecar_pb2.WriteFileRequest(path=".agent_gate/state.json", content="{}"),
    )
    assert reply is None and err is not None
    assert err.code == grpc.StatusCode.PERMISSION_DENIED and "门禁目录" in err.details

    # 阶段 1 写源码 -> PERMISSION_DENIED（先要测试用例）
    reply, err = _call(svc.WriteFile, sidecar_pb2.WriteFileRequest(path="fib.py", content="x = 1"))
    assert reply is None and err is not None and err.code == grpc.StatusCode.PERMISSION_DENIED

    # 空 path -> INVALID_ARGUMENT
    reply, err = _call(svc.WriteFile, sidecar_pb2.WriteFileRequest(path=" ", content="x"))
    assert reply is None and err is not None and err.code == grpc.StatusCode.INVALID_ARGUMENT

    # spec 写入放行
    reply, err = _call(svc.WriteFile, sidecar_pb2.WriteFileRequest(path="spec.md", content="# s"))
    assert err is None and reply is not None and reply.ok and reply.path == "spec.md"


def test_direct_exec_command_denied_then_ok(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = _servicer(ws)

    # 阶段 1 执行测试命令 -> PERMISSION_DENIED（尚未写实现）
    reply, err = _call(svc.ExecCommand, sidecar_pb2.ExecCommandRequest(command="python -m pytest -q"))
    assert reply is None and err is not None
    assert err.code == grpc.StatusCode.PERMISSION_DENIED and "实现代码" in err.details

    # 参数非法：空命令 / 越界 timeout
    reply, err = _call(svc.ExecCommand, sidecar_pb2.ExecCommandRequest(command=" "))
    assert reply is None and err is not None and err.code == grpc.StatusCode.INVALID_ARGUMENT
    reply, err = _call(svc.ExecCommand, sidecar_pb2.ExecCommandRequest(command="echo hi", timeout=99999))
    assert reply is None and err is not None and err.code == grpc.StatusCode.INVALID_ARGUMENT

    # 允许的命令可正常执行（python 解释器存在）
    reply, err = _call(
        svc.ExecCommand,
        sidecar_pb2.ExecCommandRequest(command='python -c "print(42)"', timeout=30),
    )
    assert err is None and reply is not None
    assert reply.ok and reply.exit_code == 0 and "42" in reply.output


def test_direct_record_source_change_forces_retest(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = _servicer(ws)

    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    assert _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=2))[1] is None
    (ws / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
    assert _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=3))[1] is None
    (ws / "fib.py").write_text(GOOD_IMPL, encoding="utf-8")
    assert _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=4))[1] is None
    assert _call(svc.RecordTestRun, sidecar_pb2.RecordTestRunRequest(exit_code=0, output="3 passed"))[1] is None

    # 测试后改码 -> 必须回归阶段 5
    reply, err = _call(svc.RecordSourceChange, sidecar_pb2.SourceChangeRequest(path="fib.py"))
    assert err is None and reply is not None and reply.ok and reply.path == "fib.py"
    reply, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=5))
    assert err is None and reply is not None and reply.success and reply.current_stage == 5

    # 重新测试通过后可交付
    assert _call(svc.RecordTestRun, sidecar_pb2.RecordTestRunRequest(exit_code=0, output="3 passed"))[1] is None
    reply, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=6))
    assert err is None and reply is not None and reply.success and reply.current_stage == 6

    # 空 path 与门禁目录内的路径不允许上报
    for bad in ("", ".agent_gate/state.json"):
        reply, err = _call(svc.RecordSourceChange, sidecar_pb2.SourceChangeRequest(path=bad))
        assert reply is None and err is not None and err.code == grpc.StatusCode.INVALID_ARGUMENT


def test_direct_verify_evidence(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = _servicer(ws)

    # 全新工作区 -> 校验失败（证据清单为空）
    reply, err = _call(svc.VerifyEvidence, sidecar_pb2.VerifyEvidenceRequest())
    assert err is None and reply is not None
    assert reply.ok is False and reply.violations

    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    adv, err = _call(svc.Advance, sidecar_pb2.AdvanceRequest(new_stage=2))
    assert err is None and adv.success
    reply, err = _call(svc.VerifyEvidence, sidecar_pb2.VerifyEvidenceRequest())
    assert err is None and reply.ok is True and not reply.violations


def test_direct_query_audit_filters_and_bad_params(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = _servicer(ws)

    assert _call(svc.WriteFile, sidecar_pb2.WriteFileRequest(path="spec.md", content="# s"))[1] is None
    assert _call(svc.WriteFile, sidecar_pb2.WriteFileRequest(path="fib.py", content="x"))[1] is not None

    reply, err = _call(
        svc.QueryAudit,
        sidecar_pb2.QueryAuditRequest(limit=1, event="proxy_write_denied"),
    )
    assert err is None and reply is not None and reply.total >= 1 and reply.events
    first = json.loads(reply.events[0])
    assert first["event"] == "proxy_write_denied"

    # limit 越界 / 负 offset / 非法 since / until -> INVALID_ARGUMENT
    for bad in (
        dict(limit=501),
        dict(offset=-1),
        dict(since="not-a-date"),
        dict(until="not-a-date"),
    ):
        reply, err = _call(svc.QueryAudit, sidecar_pb2.QueryAuditRequest(**bad))
        assert reply is None and err is not None and err.code == grpc.StatusCode.INVALID_ARGUMENT

    # limit=0（proto3 未设置）回落默认 50
    reply, err = _call(svc.QueryAudit, sidecar_pb2.QueryAuditRequest(limit=0))
    assert err is None and reply is not None and reply.total >= 1


def test_direct_server_factories_and_cli(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    svc = _servicer(ws)

    # 不安全端口 0 建服 / 非阻塞 serve
    server = create_grpc_server(svc.sidecar, host="127.0.0.1", port=0, max_workers=2)
    assert server is not None
    server.stop(0)
    server2 = serve_grpc(svc.sidecar, host="127.0.0.1", port=0, blocking=False)
    assert server2 is not None
    server2.stop(0)

    # CLI 主入口：serve_grpc 打桩后正常返回 0
    monkeypatch.setattr(grpc_service_mod, "serve_grpc", lambda *a, **k: None)
    rc = main(["--workspace", str(ws), "--port", "0"])
    assert rc == 0

    # grpcio 缺失路径：_require_grpc 抛错 -> 返回 1
    def _boom() -> None:
        raise RuntimeError("not installed")

    monkeypatch.setattr(grpc_service_mod, "_require_grpc", _boom)
    rc = main(["--workspace", str(ws), "--port", "0"])
    assert rc == 1