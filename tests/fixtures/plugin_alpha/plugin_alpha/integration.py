"""示例集成插件：把 phase-barrier 挂到宿主 Agent（fixture，仅测试用）。"""
from __future__ import annotations

from typing import Any


def install_alpha(agent: Any, skill: Any) -> None:
    if agent is None:
        return None
    if not hasattr(agent, "phase_barrier"):
        agent.phase_barrier = skill
    return None