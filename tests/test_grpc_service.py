"""K8s sidecar gRPC 门禁服务测试（v0.39.0）。

覆盖：in-process gRPC server 上的 8 个 RPC —— 状态查询、推进（含跳步拒绝）、
测试/源码变更上报、透明代理写入与执行（拦截返回 PERMISSION_DENIED）、
证据校验、审计查询，以及参数非法时的 INVALID_ARGUMENT。
"""
from __future__ import annotations

import json

import pytest

try:
    import grpc  # type: ignore[import-untyped]

    from anti_shortcut.grpc_service import PhaseBarrierServicer, _require_grpc
    from anti_shortcut.proto import sidecar_pb2, sidecar_pb2_grpc

    _HAVE_GRPC = True
except ModuleNotFoundError as exc:  # pragma: no cover - 可选依赖缺失
    if getattr(exc, 'name', '') == 'grpc':
        grpc = None  # type: ignore[assignment]
        _HAVE_GRPC = False
    else:
        raise
except Exception:  # pragma: no cover - 其他导入异常直接暴露，避免 CI 静默跳过
    raise

from anti_shortcut.sidecar import GateSidecar
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC

pytestmark = pytest.mark.skipif(not _HAVE_GRPC, reason="grpcio 未安装，跳过 gRPC 用例")


def _sidecar(ws, **kwargs):
    return GateSidecar(ws, **kwargs)


@pytest.fixture
def grpc_env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    sidecar = _sidecar(ws, user_request="实现斐波那契函数")
    server = grpc.server(__import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(max_workers=4))
    sidecar_pb2_grpc.add_PhaseBarrierServicer_to_server(PhaseBarrierServicer(sidecar), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel("127.0.0.1:%d" % port)
    stub = sidecar_pb2_grpc.PhaseBarrierStub(channel)
    try:
        yield ws, sidecar, stub
    finally:
        channel.close()
        server.stop(0)
        sidecar.skill.close()


def _code(exc):
    return exc.code()


def test_grpc_require_grpc_and_servicer_api():
    # 可选依赖缺失时给出明确安装提示；存在时方法齐全
    _require_grpc()
    methods = {m for m in dir(PhaseBarrierServicer) if not m.startswith("_")}
    assert {"GetState", "Advance", "RecordTestRun", "RecordSourceChange",
            "WriteFile", "ExecCommand", "VerifyEvidence", "QueryAudit"} <= methods


def test_grpc_get_state_initial_and_bad_advance(grpc_env):
    ws, sidecar, stub = grpc_env
    reply = stub.GetState(sidecar_pb2.GetStateRequest(), timeout=10)
    assert reply.current_stage == 1
    assert reply.stage_name
    assert reply.is_complete is False

    # 未写 spec 直接推进到阶段 2 -> FAILED_PRECONDITION
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=2), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.FAILED_PRECONDITION

    # new_stage 越界 -> INVALID_ARGUMENT
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=99), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.INVALID_ARGUMENT


def test_grpc_full_flow_via_rpc(grpc_env):
    """Agent 全部经 gRPC 推进：spec -> tests -> impl -> 测试通过(覆盖率) -> 交付。"""
    ws, sidecar, stub = grpc_env

    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    adv = stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=2), timeout=10)
    assert adv.success and adv.current_stage == 2

    (ws / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
    adv = stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=3), timeout=10)
    assert adv.success and adv.current_stage == 3

    (ws / "fib.py").write_text(GOOD_IMPL, encoding="utf-8")
    adv = stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=4), timeout=10)
    assert adv.success and adv.current_stage == 4

    rec = stub.RecordTestRun(
        sidecar_pb2.RecordTestRunRequest(
            exit_code=0, output="3 passed\ncoverage: 92.5% of statements\n"
        ),
        timeout=10,
    )
    assert rec.ok and rec.passed and rec.coverage == 92.5

    adv = stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=5), timeout=10)
    assert adv.success
    state = stub.GetState(sidecar_pb2.GetStateRequest(), timeout=10)
    assert state.is_complete is True and state.current_stage == 6


def test_grpc_write_file_denied_and_audit(grpc_env):
    ws, sidecar, stub = grpc_env
    # 写入门禁目录 -> PERMISSION_DENIED
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.WriteFile(sidecar_pb2.WriteFileRequest(path=".agent_gate/state.json", content="{}"), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.PERMISSION_DENIED
    assert "门禁目录" in exc_info.value.details()

    # 阶段 1 写源码 -> PERMISSION_DENIED（先要测试用例）
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.WriteFile(sidecar_pb2.WriteFileRequest(path="fib.py", content="def fib(n): return n"), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.PERMISSION_DENIED

    # 审计：最近事件含 proxy_write_denied
    audit = stub.QueryAudit(sidecar_pb2.QueryAuditRequest(limit=50), timeout=10)
    assert audit.total >= 1
    events = [json.loads(e) for e in audit.events]
    assert events[0]["event"] == "proxy_write_denied"
    assert "current_stage" in events[0]

    # 空 path -> INVALID_ARGUMENT
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.WriteFile(sidecar_pb2.WriteFileRequest(path=" ", content="x"), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.INVALID_ARGUMENT


def test_grpc_exec_denied_before_impl_and_ok_later(grpc_env):
    ws, sidecar, stub = grpc_env
    # 阶段 1 执行测试命令 -> PERMISSION_DENIED（尚未写实现）
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ExecCommand(sidecar_pb2.ExecCommandRequest(command="python -m pytest -q"), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.PERMISSION_DENIED
    assert "实现代码" in exc_info.value.details()

    # 参数非法：空命令 / 越界 timeout / 非正整数 timeout
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ExecCommand(sidecar_pb2.ExecCommandRequest(command=" "), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.INVALID_ARGUMENT
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.ExecCommand(sidecar_pb2.ExecCommandRequest(command="echo hi", timeout=99999), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.INVALID_ARGUMENT

    # 允许的命令可正常执行（python 解释器存在）
    reply = stub.ExecCommand(sidecar_pb2.ExecCommandRequest(command='python -c "print(42)"'), timeout=30)
    assert reply.ok and reply.exit_code == 0 and "42" in reply.output


def test_grpc_record_source_change_forces_retest(grpc_env):
    ws, sidecar, stub = grpc_env
    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=2), timeout=10)
    (ws / "test_fib.py").write_text(GOOD_TESTS, encoding="utf-8")
    stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=3), timeout=10)
    (ws / "fib.py").write_text(GOOD_IMPL, encoding="utf-8")
    stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=4), timeout=10)
    stub.RecordTestRun(sidecar_pb2.RecordTestRunRequest(exit_code=0, output="3 passed"), timeout=10)

    # 测试后改码 -> 必须回归阶段 5
    chg = stub.RecordSourceChange(sidecar_pb2.SourceChangeRequest(path="fib.py"), timeout=10)
    assert chg.ok and chg.path == "fib.py"
    adv = stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=5), timeout=10)
    assert adv.success and adv.current_stage == 5
    stub.RecordTestRun(sidecar_pb2.RecordTestRunRequest(exit_code=0, output="3 passed"), timeout=10)
    adv = stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=6), timeout=10)
    assert adv.success and adv.current_stage == 6

    # 门禁目录内的路径不允许上报源码变更
    with pytest.raises(grpc.RpcError) as exc_info:
        stub.RecordSourceChange(sidecar_pb2.SourceChangeRequest(path=".agent_gate/state.json"), timeout=10)
    assert _code(exc_info.value) == grpc.StatusCode.INVALID_ARGUMENT


def test_grpc_verify_evidence_after_advance(grpc_env):
    ws, sidecar, stub = grpc_env
    # 全新工作区 -> 校验失败（证据清单为空）
    result = stub.VerifyEvidence(sidecar_pb2.VerifyEvidenceRequest(), timeout=10)
    assert result.ok is False and result.violations

    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    adv = stub.Advance(sidecar_pb2.AdvanceRequest(new_stage=2), timeout=10)
    assert adv.success
    result = stub.VerifyEvidence(sidecar_pb2.VerifyEvidenceRequest(), timeout=10)
    assert result.ok is True and not result.violations
    assert result.signed in (True, False)  # HMAC 是否启用不影响结构


def test_grpc_query_audit_filters_and_bad_params(grpc_env):
    ws, sidecar, stub = grpc_env
    stub.WriteFile(sidecar_pb2.WriteFileRequest(path="spec.md", content="# s"), timeout=10)
    with pytest.raises(grpc.RpcError):
        stub.WriteFile(sidecar_pb2.WriteFileRequest(path="fib.py", content="x"), timeout=10)

    audit = stub.QueryAudit(sidecar_pb2.QueryAuditRequest(limit=1, event="proxy_write_denied"), timeout=10)
    assert audit.total >= 1 and audit.events
    first = json.loads(audit.events[0])
    assert first["event"] == "proxy_write_denied"

    # limit above max / negative offset / bad since -> INVALID_ARGUMENT
    # (proto3 int32: 0 == unset, so limit=0 falls back to default 50)
    for bad in (dict(limit=501), dict(offset=-1), dict(since="not-a-date")):
        with pytest.raises(grpc.RpcError) as exc_info:
            stub.QueryAudit(sidecar_pb2.QueryAuditRequest(**bad), timeout=10)
        assert _code(exc_info.value) == grpc.StatusCode.INVALID_ARGUMENT
    default_audit = stub.QueryAudit(sidecar_pb2.QueryAuditRequest(limit=0), timeout=10)
    assert default_audit.total >= 1