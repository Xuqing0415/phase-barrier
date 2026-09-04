"""K8s sidecar gRPC 门禁服务（v0.39.0）。

提供与 HTTP API 等价的类型化 gRPC 接口（proto 见 ``anti_shortcut/proto/sidecar.proto``）：

- ``GetState``          —— 等价 GET  /api/state
- ``Advance``           —— 等价 POST /api/advance（完整证据校验）
- ``RecordTestRun``     —— 等价 POST /api/test-run
- ``RecordSourceChange``—— 等价 POST /api/source-change
- ``WriteFile``         —— 等价 POST /api/write（拦截返回 PERMISSION_DENIED）
- ``ExecCommand``       —— 等价 POST /api/exec（拦截返回 PERMISSION_DENIED）
- ``VerifyEvidence``    —— 等价 GET  /api/verify-evidence
- ``QueryAudit``        —— 等价 GET  /api/audit

grpcio 为可选依赖：未安装时仅本模块的 gRPC 相关函数不可用（给出明确安装提示），
不影响核心包导入与 HTTP sidecar。重新生成 pb2 代码：``bash scripts/gen_grpc.sh``。

本地试运行（需 ``pip install 'phase-barrier[grpc]'``）：:

    python -m anti_shortcut.grpc_service --workspace . --port 50051
"""
from __future__ import annotations

import argparse
import json
import os
from concurrent import futures
from pathlib import Path
from typing import Any

from .proxy import ExecDenied, ProxyError, WriteDenied

try:  # 可选依赖：缺少 grpcio 时仅 gRPC 函数不可用
    import grpc  # type: ignore[import-untyped]

    from .proto import sidecar_pb2, sidecar_pb2_grpc
except ImportError:  # pragma: no cover
    grpc = None  # type: ignore[assignment]
    sidecar_pb2 = None  # type: ignore[assignment]
    sidecar_pb2_grpc = None  # type: ignore[assignment]

__all__ = ["PhaseBarrierServicer", "create_grpc_server", "serve_grpc", "main"]


def _require_grpc() -> None:
    if grpc is None:
        raise RuntimeError(
            "未安装 grpcio，无法启用 gRPC 接口；请执行 pip install 'phase-barrier[grpc]'"
        )


def _abort(context: Any, code: Any, details: str) -> None:
    """终止 RPC 并返回指定错误码（与 HTTP 语义对应）。"""
    context.abort(code, details)


# 缺少 grpcio 时退化为 object，保证模块可在零依赖环境导入
_BaseServicer = getattr(sidecar_pb2_grpc, "PhaseBarrierServicer", object)


class PhaseBarrierServicer(_BaseServicer):
    """gRPC 门禁服务实现：复用 GateSidecar 业务逻辑（锁、校验、拦截一致）。"""

    def __init__(self, sidecar: Any) -> None:
        self.sidecar = sidecar

    # ---------- 状态与推进 ----------

    def GetState(self, request: Any, context: Any) -> Any:
        st = self.sidecar.state()
        return sidecar_pb2.StateReply(
            current_stage=int(st["current_stage"]),
            stage_name=str(st["stage_name"]),
            is_complete=bool(st["is_complete"]),
            completed_stages=[int(s) for s in st["completed_stages"]],
        )

    def Advance(self, request: Any, context: Any) -> Any:
        new_stage = int(request.new_stage)
        if new_stage < 0 or new_stage > 6:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "new_stage 必须是 0-6 的整数")
        result = self.sidecar.advance(new_stage)
        if not result.get("success"):
            detail = str(result.get("error") or result.get("message") or "阶段推进未通过证据校验")
            _abort(context, grpc.StatusCode.FAILED_PRECONDITION, detail)
        st = self.sidecar.state()
        return sidecar_pb2.AdvanceReply(
            success=True,
            message=str(result.get("message") or ""),
            current_stage=int(result.get("stage", st["current_stage"])),
            stage_name=str(st["stage_name"]),
            is_complete=bool(st["is_complete"]),
        )

    # ---------- 测试运行与源码变更上报 ----------

    def RecordTestRun(self, request: Any, context: Any) -> Any:
        exit_code = int(request.exit_code)
        output = str(request.output)
        result = self.sidecar.record_test_run(exit_code, output)
        record = result.get("record") or {}
        coverage = record.get("coverage")
        return sidecar_pb2.RecordTestRunReply(
            ok=True,
            passed=bool(record.get("passed")),
            summary=str(record.get("summary") or ""),
            coverage=float(coverage) if coverage is not None else 0.0,
        )

    def RecordSourceChange(self, request: Any, context: Any) -> Any:
        path = str(request.path)
        if not path:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "path 必须是非空字符串")
        if self.sidecar.skill._in_gate_dir(path):
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "path 不允许指向门禁目录.agent_gate")
        self.sidecar.record_source_change(path)
        return sidecar_pb2.SourceChangeReply(ok=True, path=path)

    # ---------- 透明代理 ----------

    def WriteFile(self, request: Any, context: Any) -> Any:
        path = str(request.path)
        content = str(request.content)
        if not path.strip():
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "path 必须是非空字符串")
        try:
            result = self.sidecar.write_file(path, content)
        except WriteDenied as exc:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, exc.reason)
        except ProxyError as exc:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        return sidecar_pb2.WriteFileReply(ok=bool(result.get("ok", True)), path=path)

    def ExecCommand(self, request: Any, context: Any) -> Any:
        command = str(request.command)
        if not command.strip():
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "command 必须是非空字符串")
        timeout = int(request.timeout) if request.timeout else None
        if timeout is not None and not 1 <= timeout <= 3600:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "timeout 必须是 1-3600 的整数秒")
        cwd = str(request.cwd) if request.cwd else None
        try:
            result = self.sidecar.execute_command(command, timeout=timeout, cwd=cwd)
        except ExecDenied as exc:
            _abort(context, grpc.StatusCode.PERMISSION_DENIED, exc.reason)
        except ProxyError as exc:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        return sidecar_pb2.ExecCommandReply(
            ok=bool(result.get("ok", True)),
            exit_code=int(result.get("exit_code") or 0),
            output=str(result.get("output") or ""),
            passed=bool(result.get("passed")),
            summary=str(result.get("summary") or ""),
        )

    # ---------- 证据与审计 ----------

    def VerifyEvidence(self, request: Any, context: Any) -> Any:
        result = self.sidecar.verify_evidence()
        return sidecar_pb2.VerifyEvidenceReply(
            ok=bool(result.get("ok")),
            violations=[str(v) for v in result.get("violations") or []],
            signed=bool(result.get("signed")),
        )

    def QueryAudit(self, request: Any, context: Any) -> Any:
        from .sidecar import _parse_event_time

        limit = int(request.limit) if request.limit else 50
        if not 1 <= limit <= 500:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "limit 必须是 1-500 的整数")
        offset = int(request.offset) if request.offset else 0
        if offset < 0:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "offset 必须是非负整数")
        since = str(request.since) if request.since else None
        until = str(request.until) if request.until else None
        if since is not None and _parse_event_time(since) is None:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "since 必须是 ISO 时间戳")
        if until is not None and _parse_event_time(until) is None:
            _abort(context, grpc.StatusCode.INVALID_ARGUMENT, "until 必须是 ISO 时间戳")
        result = self.sidecar.audit(
            limit=limit,
            offset=offset,
            since=since,
            until=until,
            event=str(request.event) if request.event else None,
        )
        return sidecar_pb2.QueryAuditReply(
            total=int(result.get("total") or 0),
            events=[json.dumps(ev, ensure_ascii=False) for ev in result.get("events") or []],
        )


def create_grpc_server(
    sidecar: Any,
    host: str = "0.0.0.0",
    port: int = 50051,
    tls_cert: str | None = None,
    tls_key: str | None = None,
    tls_client_ca: str | None = None,
    max_workers: int = 10,
) -> Any:
    """构建并启动 gRPC 服务器（返回已 ``start()`` 的 server 实例）。"""
    _require_grpc()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    sidecar_pb2_grpc.add_PhaseBarrierServicer_to_server(PhaseBarrierServicer(sidecar), server)
    if tls_cert and tls_key:
        creds = grpc.ssl_server_credentials(
            ((tls_key.encode("utf-8"), tls_cert.encode("utf-8")),),
            root_certificates=tls_client_ca.encode("utf-8") if tls_client_ca else None,
            require_client_auth=bool(tls_client_ca),
        )
        server.add_secure_port(f"{host}:{port}", creds)
    else:
        server.add_insecure_port(f"{host}:{port}")
    server.start()
    return server


def serve_grpc(
    sidecar: Any,
    host: str = "0.0.0.0",
    port: int = 50051,
    tls_cert: str | None = None,
    tls_key: str | None = None,
    tls_client_ca: str | None = None,
    blocking: bool = True,
) -> Any:
    """启动 gRPC 门禁服务；``blocking=False`` 时立即返回已启动的 server。"""
    server = create_grpc_server(
        sidecar,
        host=host,
        port=port,
        tls_cert=tls_cert,
        tls_key=tls_key,
        tls_client_ca=tls_client_ca,
    )
    if blocking:
        server.wait_for_termination()
    return server


def main(argv: list[str] | None = None) -> int:
    """独立启动 gRPC 门禁服务：``python -m anti_shortcut.grpc_service``。"""
    parser = argparse.ArgumentParser(description="phase-barrier K8s sidecar gRPC 门禁服务")
    parser.add_argument("--workspace", default=".", help="工作区路径（与 Agent 共享的卷）")
    parser.add_argument("--config", default=None, help="phase-barrier YAML 配置路径（可选）")
    parser.add_argument("--user-request", default="", help="用户需求原文（阶段 0 证据）")
    parser.add_argument(
        "--state-key",
        default="",
        help="状态签名 HMAC 密钥（等价于环境变量 PHASE_BARRIER_HMAC_KEY）",
    )
    parser.add_argument("--tls-cert", default="", help="mTLS 服务端证书 PEM")
    parser.add_argument("--tls-key", default="", help="mTLS 服务端私钥 PEM")
    parser.add_argument("--tls-client-ca", default="", help="客户端证书签发 CA（PEM），启用后强制客户端证书")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=50051, help="监听端口")
    args = parser.parse_args(argv)
    if args.state_key:
        os.environ["PHASE_BARRIER_HMAC_KEY"] = args.state_key
    try:
        _require_grpc()
    except RuntimeError as exc:
        print(f"[grpc] {exc}", file=__import__("sys").stderr)
        return 1
    from .sidecar import GateSidecar

    sidecar = GateSidecar(
        Path(args.workspace),
        config=Path(args.config) if args.config else None,
        user_request=args.user_request,
    )
    serve_grpc(
        sidecar,
        host=args.host,
        port=args.port,
        tls_cert=args.tls_cert or None,
        tls_key=args.tls_key or None,
        tls_client_ca=args.tls_client_ca or None,
        blocking=True,
    )
    return 0
