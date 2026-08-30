"""反捷径校验 Skill 核心类（阶段门禁 + 工具拦截 + 证据校验）。

嵌入方式：在 Agent 启动时创建 ``AntiShortcutSkill``，用 ``install`` 或
``wrap_write_file`` / ``wrap_execute_command`` 包装 Agent 暴露的工具，
并注入 ``advance_stage`` 专用工具。Agent 只能通过包装后的工具与状态机交互，
无法通过自然语言指令或直接文件操作绕过门禁。

v0.9.0：审计远程推送（SIEM）、证据签名清单、HMAC 密钥轮换。
"""
from __future__ import annotations

import atexit
import functools
import os
from pathlib import Path
from typing import Any, Callable

from .audit import get_audit_logger
from .config import STAGES, GateConfig, load_config
from .evidence import EVIDENCE_MANIFEST_NAME, EvidenceManifest
from .interceptors import (
    extract_written_paths,
    is_language_test_command,
    summarize_test_output,
    touches_gate_dir,
)
from .languages import get_adapter
from .remote_audit import RemoteAuditSink
from .state import StateManager
from .validators import (
    validate_implementation,
    validate_retest,
    validate_spec,
    validate_test_run,
    validate_tests,
)

__all__ = ["AntiShortcutSkill"]


class AntiShortcutSkill:
    """阶段门禁 Skill：强制编码 Agent 按“需求→spec→测试→实现→测试→修复→交付”推进。

    :param workspace: 工作区根目录（所有证据文件都在其下）
    :param config: 配置（GateConfig / YAML 路径 / dict / None）
    :param user_request: 用户需求原文（阶段 0 证据，由系统传入）
    :param console_log: 是否同时向控制台输出审计日志
    """

    def __init__(
        self,
        workspace: str | Path,
        config: GateConfig | dict | str | Path | None = None,
        user_request: str = "",
        *,
        adapter: Any | None = None,
        console_log: bool = False,
    ) -> None:
        self.config = load_config(config)
        self.workspace = Path(workspace).resolve()
        self.config.workspace = self.workspace
        self.adapter = adapter or get_adapter(self.config, self.workspace)
        self.gate_dir = self.workspace / self.config.gate_dir_name
        self.gate_dir.mkdir(parents=True, exist_ok=True)
        self.state = StateManager(
            self.gate_dir / self.config.state_file_name,
            user_request=user_request,
            hmac_key=self.config.state_hmac_key,
            hmac_keys=self.config.state_hmac_keys,
        )
        # 审计远程推送（v0.9.0）：配置 audit_remote_url 后异步转发到 SIEM / webhook
        self.remote_sink: RemoteAuditSink | None = None
        if self.config.audit_remote_url:
            self.remote_sink = RemoteAuditSink(
                self.config.audit_remote_url,
                token=self.config.audit_remote_token,
                timeout=self.config.audit_remote_timeout,
                batch_size=self.config.audit_remote_batch_size,
                max_queue=self.config.audit_remote_max_queue,
                flush_interval=self.config.audit_remote_flush_interval,
                ca_bundle=self.config.audit_remote_ca_bundle,
                retries=self.config.audit_remote_retries,
                backoff_factor=self.config.audit_remote_backoff_factor,
                client_cert=self.config.audit_remote_client_cert,
                client_key=self.config.audit_remote_client_key,
                headers=self.config.audit_remote_headers,
                spool_dir=self.config.audit_remote_spool_dir,
            )
        self.logger = get_audit_logger(
            self.gate_dir / self.config.audit_log_name,
            console=console_log,
            remote=self.remote_sink,
        )
        # 证据签名清单（v0.9.0）：记录每个阶段推进时的证据文件哈希，独立于 state.json
        self.evidence_manifest = EvidenceManifest(
            self.gate_dir / EVIDENCE_MANIFEST_NAME,
            hmac_key=self.config.state_hmac_key or os.environ.get("PHASE_BARRIER_HMAC_KEY"),
        )
        self.validators: dict[int, Callable] = {
            1: validate_spec,
            2: validate_tests,
            3: validate_implementation,
            4: validate_test_run,
            5: validate_retest,
        }
        self._protect_gate_dir()
        if user_request:
            self.state.record_user_request(user_request)
        self.logger.info("skill_initialized", workspace=str(self.workspace), **self._stage_summary())
        atexit.register(self.close)

    # ---------- 状态查询 ----------

    @property
    def current_stage(self) -> int:
        return self.state.current_stage

    @property
    def stage_name(self) -> str:
        return STAGES.get(self.current_stage, str(self.current_stage))

    @property
    def is_complete(self) -> bool:
        return self.state.is_complete

    def _stage_summary(self) -> dict:
        return {
            "current_stage": self.current_stage,
            "stage_name": self.stage_name,
            "completed_stages": self.state.completed_stages,
        }

    # ---------- 门禁目录保护 ----------

    def _protect_gate_dir(self) -> None:
        """门禁目录防护说明（进程内不 chmod，避免锁死 Skill 自身的原子写入）。

        生产环境建议把 ``.agent_gate`` 以只读卷挂载给 Agent 的执行容器
        （例如 Docker ``-v /host/path:/.agent_gate:ro``），使 Agent 侧即使绕过
        工具包装也无法修改状态文件。本进程内的强制边界是：

        - 状态文件由 Skill 独占原子写入；
        - Agent 可用的 ``write_file`` / ``execute_command`` 均被包装，
          任何指向门禁目录的写入与 shell 访问都会被拦截；
        - 证据文件在推进时记录 SHA-256（v0.9.0 写入独立签名清单），
          事后篡改可被检测。
        """
        if self.config.protect_gate_dir:
            self.logger.info(
                "gate_dir_policy",
                gate_dir=str(self.gate_dir),
                note="生产环境请将 .agent_gate 挂载为 Agent 只读卷，实现进程级隔离",
            )

    # ---------- 权限检查 ----------

    def _in_gate_dir(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        try:
            p.resolve().relative_to(self.gate_dir.resolve())
            return True
        except (ValueError, OSError):
            return False

    def _classify_path(self, path: str | Path) -> str:
        """按当前语言适配器把路径分类为 test / source / other。"""
        p = Path(path)
        if self.adapter.is_test_file(p, self.config):
            return "test"
        if self.adapter.is_source_file(p, self.config):
            return "source"
        return "other"

    def check_write_permission(self, path: str | Path) -> None:
        """检查写入权限；不允许时抛出 PermissionError（作为工具拦截反馈）。"""
        p = Path(path)
        if self._in_gate_dir(p):
            raise PermissionError("禁止写入门禁目录 .agent_gate：状态与证据由 Skill 独占管理")
        kind = self._classify_path(p)
        stage = self.current_stage
        if kind == "test" and stage < 1:
            raise PermissionError(
                "当前阶段不允许编写测试文件：请先完成 spec 设计（阶段 1）并通过 advance_stage 校验"
            )
        if kind == "source" and stage < 2:
            raise PermissionError(
                "当前阶段不允许编写实现代码：请先完成测试用例编写（阶段 2）并通过 advance_stage 校验"
            )
        if kind == "other" and not self.config.allow_other_files_any_stage:
            raise PermissionError("当前配置禁止写入其他类型文件")

    def check_exec_permission(self, command: str | list[str]) -> None:
        """检查 shell 命令执行权限；不允许时抛出 PermissionError。"""
        cmd = command if isinstance(command, str) else " ".join(str(c) for c in command)
        if touches_gate_dir(cmd, self.gate_dir):
            raise PermissionError("禁止通过 shell 访问门禁目录 .agent_gate")
        if is_language_test_command(cmd, self.config, self.adapter) and self.current_stage < 3:
            raise PermissionError(
                "当前阶段不允许运行测试命令：请先完成实现代码（阶段 3）并通过 advance_stage 校验"
            )
        for written in extract_written_paths(cmd):
            if written.startswith("<"):
                continue
            self.check_write_permission(written)

    # ---------- 工具包装 ----------

    def wrap_write_file(self, original_write: Callable) -> Callable:
        """包装 ``write_file(path, content)``：写入前做阶段门禁检查，写入后记录变更时间戳。"""

        @functools.wraps(original_write)
        def guarded(path: str | Path, content: Any, **kwargs: Any) -> Any:
            self.check_write_permission(path)
            result = original_write(path, content, **kwargs)
            kind = self._classify_path(Path(path))
            if kind in ("test", "source"):
                self.state.mark_source_change(str(path))
                self.logger.info("file_written", path=str(path), kind=kind, **self._stage_summary())
            return result

        return guarded

    def wrap_execute_command(self, original_exec: Callable) -> Callable:
        """包装 ``execute_command(command)``：执行前做命令门禁检查，执行后记录测试结果。

        底层工具约定返回 ``{"exit_code": int, "output": str}``（或包含这两个键的 dict）；
        若返回纯文本则按输出启发式推断结果。
        """

        @functools.wraps(original_exec)
        def guarded(command: str | list[str], **kwargs: Any) -> Any:
            cmd = command if isinstance(command, str) else " ".join(str(c) for c in command)
            self.check_exec_permission(command)
            result = original_exec(command, **kwargs)
            if is_language_test_command(cmd, self.config, self.adapter):
                record = self._record_test_run(result)
                self.state.mark_test_run(record)
                self.logger.info(
                    "test_run_recorded",
                    exit_code=record.get("exit_code"),
                    passed=record.get("passed"),
                    **self._stage_summary(),
                )
            return result

        return guarded

    def _record_test_run(self, result: Any) -> dict:
        if isinstance(result, dict):
            exit_code = result.get("exit_code", result.get("exitcode"))
            output = (
                result.get("output")
                or result.get("stdout")
                or result.get("stderr")
                or ""
            )
        else:
            exit_code = None
            output = str(result or "")
        if not isinstance(output, str):
            output = str(output)
        return summarize_test_output(
            output,
            exit_code,
            max_tail=self.config.max_test_output_tail,
            adapter=self.adapter,
        )

    def install(self, tools: dict[str, Callable]) -> dict[str, Callable]:
        """把包装后的工具注入 Agent 的工具表（原地修改并返回）。

        :param tools: Agent 的工具注册表，如 ``{"write_file": ..., "execute_command": ...}``
        :return: 注入 ``advance_stage`` 后的工具表
        """
        if "write_file" in tools:
            tools["write_file"] = self.wrap_write_file(tools["write_file"])
        if "execute_command" in tools:
            tools["execute_command"] = self.wrap_execute_command(tools["execute_command"])
        tools["advance_stage"] = self.advance_stage
        return tools

    # ---------- 证据签名清单（v0.9.0） ----------

    def _record_evidence_signatures(self, stage: int, evidence: dict) -> None:
        """把校验器收集的 sha256 写入独立证据清单，供交付 / CI 事后比对。"""
        if not self.config.evidence_signing:
            return
        sha = evidence.get("sha256")
        files: dict[str, str] = {}
        if isinstance(sha, dict):
            files.update({str(k): str(v) for k, v in sha.items()})
        elif isinstance(sha, str) and evidence.get("file"):
            files[str(evidence["file"])] = sha
        if files:
            self.evidence_manifest.record(stage, files)
            self.logger.info(
                "evidence_signed",
                stage=stage,
                files=sorted(files),
                **self._stage_summary(),
            )

    def verify_evidence(self) -> tuple[bool, list[str]]:
        """对照工作区当前文件校验证据清单，返回 (是否通过, 违规列表)。

        :raises EvidenceManifestError: 清单损坏 / 签名不匹配（加载时抛出）
        """
        return self.evidence_manifest.verify(self.workspace)

    # ---------- 生命周期 ----------

    def close(self) -> None:
        """释放资源：冲刷并关闭远程审计推送（无远程推送时为 no-op，幂等）。"""
        if self.remote_sink is not None:
            self.remote_sink.close()
            self.remote_sink = None

    # ---------- 阶段推进 ----------

    def advance_stage(self, new_stage: int, evidence: dict | None = None) -> dict:
        """阶段推进专用工具：校验“只能进入当前阶段 + 1”，并验证当前阶段的证据。

        特殊分支：阶段 4（运行测试）推进时——
        - 最近一次测试通过且无后续代码变更 → 直接进入阶段 6（交付，跳过修复）
        - 测试未通过 / 代码在测试后被修改 → 进入阶段 5（修复与回归）
        """
        cur = self.current_stage
        if self.is_complete:
            return {"success": False, "stage": cur, "error": "任务已完成（阶段 6 交付），无需继续推进"}
        if new_stage != cur + 1:
            return {
                "success": False,
                "stage": cur,
                "error": (
                    f"不允许跳跃阶段：当前阶段 {cur}（{STAGES.get(cur)}），"
                    f"只能进入阶段 {cur + 1}（{STAGES.get(cur + 1)}）"
                ),
            }

        validator = self.validators.get(cur)
        if validator is None:
            if cur == 0:
                ok, msg, ev = True, "需求已记录", {"user_request": self.state.get_evidence("user_request")}
            else:
                return {"success": False, "stage": cur, "error": f"阶段 {cur} 无对应校验器"}
        else:
            ok, msg, ev = validator(self.workspace, self.config, self.state, self.adapter)
        if evidence:
            ev = {**(ev or {}), **evidence}

        if not ok:
            self.logger.warning("stage_advance_rejected", stage=cur, reason=msg, evidence=ev)
            return {"success": False, "stage": cur, "error": msg, "evidence": ev}

        # 阶段 4 的特殊分支：按测试结果决定进入 5 还是跳过到 6
        if cur == 4:
            tr = self.state.get_evidence("last_test_run") or {}
            changed_at = self.state.get_evidence("last_source_change_at_epoch")
            ran_at = tr.get("at_epoch")
            if tr.get("passed") and (changed_at is None or (ran_at is not None and ran_at >= changed_at)):
                new_stage = 6
                msg = "测试全部通过，跳过修复阶段，直接进入交付"
            else:
                new_stage = 5
                msg = "测试未通过或代码在测试后被修改，进入修复与回归阶段"

        self.state.advance(new_stage, ev)
        self._record_evidence_signatures(cur, ev)
        self.logger.info("stage_advanced", from_stage=cur, to_stage=new_stage, message=msg, evidence=ev)
        return {
            "success": True,
            "stage": new_stage,
            "message": f"已进入阶段 {new_stage}（{STAGES.get(new_stage)}）：{msg}",
            "evidence": ev,
        }