"""五道防线基类与结果对象（独立模块，避免与包 __init__ 循环导入）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import GateConfig


@dataclass
class DefenseCheckResult:
    """一道防线的一次检查结果。

    :param ok: 是否通过（False 会阻止阶段推进）
    :param message: 供 Agent / 人工阅读的中文结论与修复建议
    :param evidence: 结构化明细（与 state 阶段证据合并留痕）
    :param request_id: 命中人工复核时生成 / 使用的复核请求 ID
    """

    ok: bool
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None


class DefenseLine:
    """防线基类契约。

    子类提供：``name``（对应 ``config.defense.<name>`` 配置节）、
    ``trigger``（触发推进组合 ``(from_stage, to_stage)`` 列表）与
    ``run(workspace, config, state, from_stage, to_stage)``。
    """

    name: str = ""
    trigger: tuple[tuple[int, int], ...] = ()

    def run(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        from_stage: int,
        to_stage: int,
    ) -> DefenseCheckResult:
        raise NotImplementedError

    def enabled(self, config: GateConfig) -> bool:
        section = getattr(config.defense, self.name, None)
        return bool(getattr(section, "enabled", False))

    def in_trigger(self, from_stage: int, to_stage: int) -> bool:
        return (from_stage, to_stage) in self.trigger
