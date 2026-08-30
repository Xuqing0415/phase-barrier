"""编排器侧轻量 SDK：为 Alpha-SWE 等 Agent 平台提供稳定的阶段门禁集成面。

与 ``AntiShortcutSkill`` 的分工：
- ``AntiShortcutSkill``（v0.1.0）面向“工具调用层”：包装 ``write_file`` /
  ``execute_command`` 实时拦截 Agent 的跳步行为；
- ``PhaseBarrier``（v0.22.0）面向“编排器钩子”：在任务启动 / 阶段切换钩子处
  调用，传入项目目录与 Agent 声称的阶段，返回“是否放行 + 约束提示”，
  由编排器把提示回传给 Agent，强制补全 spec / test 等前置证据。

校验逻辑全部留在本包维护，编排器只做调用（职责分离）。所有方法返回
JSON 可序列化的 dict，字段稳定，编排器可直接透传 / 落日志 / 回传 Agent。

用法::

    from anti_shortcut import PhaseBarrier

    barrier = PhaseBarrier(workspace=project_dir, user_request=user_request)

    # 任务启动钩子：Agent 声称从阶段 1（spec 设计）开始
    gate = barrier.check(1)
    if not gate["allowed"]:
        prompt = gate["message"]   # 喂回给 Agent，强制补全前置证据

    # 阶段切换钩子：Agent 声称已完成阶段 1，申请进入阶段 2
    result = barrier.advance(2)
    if not result["success"]:
        prompt = result["error"]

返回结构（稳定）：
- ``inspect()``         -> {workspace, current_stage, stage_name, completed_stages, complete, last_test_run}
- ``check(stage)``      -> {allowed, stage, stage_name, current_stage, message, violations}
- ``advance(to_stage)`` -> {success, stage, stage_name, message, error, evidence}
- ``verify_evidence()`` -> {ok, violations, signed}

向后兼容：``PhaseBarrier`` 包装 ``AntiShortcutSkill``，后者全部行为与历史
版本一致；``PhaseBarrier()`` 无参调用默认使用当前工作目录。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import STAGES, GateConfig
from .evidence import EvidenceManifestError
from .skill import AntiShortcutSkill
from .validators import get_validator

__all__ = ["PhaseBarrier"]


class PhaseBarrier:
    """编排器侧阶段门禁 SDK（v0.22.0）。

    :param workspace: 项目目录（默认当前工作目录，支持无参调用）
    :param config: 配置（GateConfig / YAML 路径 / dict / None）
    :param user_request: 用户需求原文（阶段 0 证据，由系统传入）
    :param console_log: 是否同时在控制台输出审计日志
    """

    def __init__(
        self,
        workspace: str | Path = ".",
        config: GateConfig | dict | str | Path | None = None,
        user_request: str = "",
        *,
        console_log: bool = False,
    ) -> None:
        self.skill = AntiShortcutSkill(
            workspace,
            config=config,
            user_request=user_request,
            console_log=console_log,
        )
        self.workspace = self.skill.workspace
        self.config = self.skill.config

    # ---------- 状态查询 ----------

    def inspect(self) -> dict:
        """当前门禁状态快照（只读）。"""
        tr = self.skill.state.get_evidence("last_test_run") or {}
        return {
            "workspace": str(self.workspace),
            "current_stage": self.skill.current_stage,
            "stage_name": self.skill.stage_name,
            "completed_stages": list(self.skill.state.completed_stages),
            "complete": self.skill.is_complete,
            "last_test_run": (
                {k: tr.get(k) for k in ("exit_code", "passed", "summary")} if tr else None
            ),
        }

    # ---------- 钩子校验 ----------

    def check(self, stage: int) -> dict:
        """编排器钩子：Agent 声称要进入 / 处于 ``stage``，返回是否放行及约束提示。

        只读操作，不修改状态。``allowed=True`` 表示前置证据已满足，可放行；
        否则 ``violations`` 列出缺失项，``message`` 可直接回传给 Agent。

        :param stage: Agent 声称的阶段号（0-6）
        """
        cur = self.skill.current_stage
        stage_name = (
            STAGES.get(stage, str(stage)) if isinstance(stage, int) else str(stage)
        )
        base = {
            "stage": stage,
            "stage_name": stage_name,
            "current_stage": cur,
            "message": "",
            "violations": [],
        }
        if isinstance(stage, bool) or not isinstance(stage, int):
            msg = f"stage 必须是 0-6 的整数，收到 {stage!r}"
            return {**base, "allowed": False, "message": msg, "violations": [msg]}
        if stage < 0 or stage > 6:
            msg = f"stage 必须在 0-6 之间，收到 {stage}"
            return {**base, "allowed": False, "message": msg, "violations": [msg]}
        if cur >= stage:
            return {
                **base,
                "allowed": True,
                "message": (
                    f"当前阶段 {cur}（{STAGES.get(cur)}）已满足阶段 {stage} 的前置要求，放行"
                ),
            }
        required = stage - 1
        if cur < required:
            missing = " -> ".join(
                f"{i}（{STAGES.get(i)}）" for i in range(cur, required + 1)
            )
            msg = (
                f"跳步：需先完成阶段 {missing} 并通过校验，"
                f"当前处于阶段 {cur}（{STAGES.get(cur)}）"
            )
            return {
                **base,
                "allowed": False,
                "message": msg,
                "violations": [f"缺少阶段 {cur} 至 {required} 的证据"],
            }
        validator = get_validator(cur)
        if validator is None:
            ok, msg = True, "前置证据已满足"
        else:
            ok, msg, _ev = validator(
                self.workspace, self.config, self.skill.state, self.skill.adapter
            )
        if ok:
            return {
                **base,
                "allowed": True,
                "message": f"放行：可进入阶段 {stage}（{STAGES.get(stage)}）",
            }
        return {
            **base,
            "allowed": False,
            "message": f"未通过校验：{msg}",
            "violations": [msg],
        }

    # ---------- 阶段推进 ----------

    def advance(self, to_stage: int) -> dict:
        """编排器钩子：校验当前阶段证据并推进到 ``to_stage``。

        与 Agent 内部 ``advance_stage`` 走同一套证据校验；返回结构增加
        稳定的 ``stage_name`` 字段，其余字段（success / stage / message /
        error / evidence）与历史一致。
        """
        result = self.skill.advance_stage(to_stage)
        result.setdefault(
            "stage_name",
            STAGES.get(result.get("stage"), str(result.get("stage"))),
        )
        return result

    def record_test_run(self, result: dict | Any) -> dict:
        """编排器钩子：登记一次测试运行结果（``{exit_code, output}``）。

        编排器执行测试命令后调用，barrier 解析输出并写入状态机，
        供阶段 4 推进（测试全部通过 -> 交付 / 未通过 -> 修复）校验使用。
        与工具级拦截中 ``execute_command`` 包装的自动记录等价。
        """
        record = self.skill._record_test_run(result)
        self.skill.state.mark_test_run(record)
        self.skill.logger.info(
            "test_run_recorded",
            exit_code=record.get("exit_code"),
            passed=record.get("passed"),
            **self.skill._stage_summary(),
        )
        return record
    def verify_evidence(self) -> dict:
        """对照工作区校验证据签名清单，返回 ``{ok, violations, signed}``。

        清单缺失 / 签名不匹配等异常统一捕为 ``ok=False``，不抛异常，
        便于编排器钩子直接判断。
        """
        try:
            ok, violations = self.skill.verify_evidence()
            return {"ok": ok, "violations": list(violations), "signed": True}
        except EvidenceManifestError as exc:
            return {"ok": False, "violations": [str(exc)], "signed": False}

    # ---------- 可选：工具级拦截 ----------

    def install(self, tools: dict[str, Any]) -> dict[str, Any]:
        """可选：同时包装 Agent 工具做实时拦截（与钩子校验叠加使用）。"""
        return self.skill.install(tools)

    def close(self) -> None:
        """释放资源（刷新 / 关闭远端审计通道）。"""
        self.skill.close()
