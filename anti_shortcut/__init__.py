"""反捷径校验 Skill：强制编码 Agent 遵循标准工程师 SOP（阶段门禁）。

用法::

    from anti_shortcut import AntiShortcutSkill

    skill = AntiShortcutSkill(workspace=".", user_request="实现一个斐波那契函数")
    tools = skill.install({"write_file": my_write, "execute_command": my_exec})
    result = tools["advance_stage"](2)  # 完成 spec 后推进
"""
from importlib.metadata import PackageNotFoundError, version as _distribution_version

from .config import STAGES, GateConfig, load_config
from .integration import bootstrap, install_into, load_plugins, register_integration
from .languages import (
    LANGUAGE_REGISTRY,
    JavaScriptAdapter,
    LanguageAdapter,
    PythonAdapter,
    detect_language,
    get_adapter,
)
from .skill import AntiShortcutSkill
from .state import StateManager

try:
    __version__ = _distribution_version("phase-barrier")
except PackageNotFoundError:  # 直接从源码运行（未安装）时的占位版本
    __version__ = "0.0.0.dev0"

__all__ = [
    "AntiShortcutSkill",
    "StateManager",
    "GateConfig",
    "STAGES",
    "load_config",
    "bootstrap",
    "install_into",
    "load_plugins",
    "register_integration",
    "LanguageAdapter",
    "PythonAdapter",
    "JavaScriptAdapter",
    "LANGUAGE_REGISTRY",
    "detect_language",
    "get_adapter",
    "__version__",
]
