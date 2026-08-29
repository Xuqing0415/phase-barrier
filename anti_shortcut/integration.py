"""集成层：插件加载与通用 Agent 工具注册表接入。

对接方式（对应方案“技术栈选型—集成与部署”）：

1. **一键接入**：``bootstrap(agent_tools, workspace, ...)`` 创建 Skill、包装工具、
   注入 ``advance_stage`` 并加载集成插件，适合 Alpha-SWE 等以“工具表 dict”暴露工具的 Agent。
2. **进程内插件**：``register_integration(name, installer)`` 注册，``installer(agent, skill)``
   负责把包装后的工具装回 Agent；宿主进程启动时调用 ``load_plugins(agent, skill)``。
3. **入口点插件**：发布为独立包的插件可声明 ``anti_shortcut.integrations`` 入口点，
   ``load_plugins`` 自动发现并执行其 ``install``。
"""
from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import Any, Callable

from .skill import AntiShortcutSkill

__all__ = [
    "ENTRY_POINT_GROUP",
    "bootstrap",
    "install_into",
    "load_plugins",
    "register_integration",
]

ENTRY_POINT_GROUP = "anti_shortcut.integrations"

# 进程内集成注册表：name -> installer(agent, skill)
_registry: dict[str, Callable[[Any, AntiShortcutSkill], None]] = {}


def register_integration(
    name: str,
    installer: Callable[[Any, AntiShortcutSkill], None],
) -> None:
    """进程内注册一个集成插件。

    :param name: 插件名（重复注册会覆盖）
    :param installer: ``installer(agent, skill)``，负责把 ``skill`` 包装后的工具装回 ``agent``
    """
    if not callable(installer):
        raise TypeError("installer 必须可调用")
    _registry[name] = installer


def load_plugins(
    agent: Any = None,
    skill: AntiShortcutSkill | None = None,
) -> list[str]:
    """加载并执行所有集成插件（进程内注册表 + 已安装包的入口点）。

    :param agent: 宿主 Agent 对象（原样传给 installer）
    :param skill: 已创建的 Skill 实例
    :return: 已加载的插件名列表
    """
    loaded: list[str] = []
    for name, installer in _registry.items():
        installer(agent, skill)
        loaded.append(name)
    try:
        entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
    except (TypeError, importlib.metadata.PackageNotFoundError):  # pragma: no cover
        entry_points = ()
    for ep in entry_points:
        plugin = ep.load()
        installer = plugin if callable(plugin) else getattr(plugin, "install", None)
        if installer is None:
            raise TypeError(
                f"入口点插件 {ep.name!r} 必须整体可调用，或导出 install(agent, skill)"
            )
        installer(agent, skill)
        loaded.append(ep.name)
    return loaded


def install_into(
    agent_tools: dict[str, Callable],
    skill: AntiShortcutSkill,
) -> dict[str, Callable]:
    """把 Skill 包装后的工具写回 Agent 工具表（等价于 ``skill.install``）。"""
    return skill.install(agent_tools)


def bootstrap(
    agent_tools: dict[str, Callable],
    workspace: str | Path,
    config: Any = None,
    user_request: str = "",
    *,
    agent: Any = None,
    load_integrations: bool = True,
) -> AntiShortcutSkill:
    """一步完成：创建 Skill -> 包装 Agent 工具 -> 加载集成插件。

    :param agent_tools: Agent 的工具表，如 ``{"write_file": ..., "execute_command": ...}``
    :param workspace: 工作区根目录
    :param config: Skill 配置（YAML 路径 / dict / GateConfig / None）
    :param user_request: 用户需求原文（阶段 0 证据）
    :param agent: 宿主 Agent 对象（透传给集成插件）
    :param load_integrations: 是否加载已注册的集成插件
    """
    skill = AntiShortcutSkill(workspace, config=config, user_request=user_request)
    install_into(agent_tools, skill)
    if load_integrations:
        load_plugins(agent=agent, skill=skill)
    return skill
