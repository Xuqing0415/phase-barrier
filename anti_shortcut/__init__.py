"""反捷径校验 Skill：强制编码 Agent 遵循标准工程师 SOP（阶段门禁）。

用法::

    from anti_shortcut import AntiShortcutSkill

    skill = AntiShortcutSkill(workspace=".", user_request="实现一个斐波那契函数")
    tools = skill.install({"write_file": my_write, "execute_command": my_exec})
    result = tools["advance_stage"](2)  # 完成 spec 后推进
"""
from importlib.metadata import PackageNotFoundError, version as _distribution_version

from .config import STAGES, STAGE_META, GateConfig, load_config
from .evidence import (
    EVIDENCE_MANIFEST_NAME,
    EvidenceManifest,
    EvidenceManifestError,
    EvidenceTamperedError,
)
from .integration import bootstrap, install_into, load_plugins, register_integration
from .interceptors import (
    INTERCEPTOR_ENTRY_POINT_GROUP,
    evaluate_rules,
    load_rule_plugins,
    register_rule,
)
from .remote_audit import RemoteAuditSink
from .rules import BUILTIN_RULES, RULE_DESCRIPTIONS
from .semantic import (
    BUILTIN_SEMANTIC_VALIDATORS,
    SEMANTIC_VALIDATOR_ENTRY_POINT_GROUP,
    MutationScoreValidator,
    RequirementCoverageValidator,
    SemanticCheckResult,
    SemanticValidator,
    load_semantic_plugins,
    register_semantic_validator,
    run_semantic_checks,
)
from .languages import (
    CppAdapter,
    CSharpAdapter,
    DotNetAdapter,
    LANGUAGE_REGISTRY,
    JavaScriptAdapter,
    LanguageAdapter,
    PythonAdapter,
    RubyAdapter,
    detect_language,
    get_adapter,
)
from .sdk import PhaseBarrier, classify_stage_path
from .skill import AntiShortcutSkill
from .state import StateManager
from .validators import (
    BUILTIN_VALIDATORS,
    VALIDATOR_ENTRY_POINT_GROUP,
    get_validator,
    load_validator_plugins,
    register_validator,
)

try:
    __version__ = _distribution_version("phase-barrier")
except PackageNotFoundError:  # 直接从源码运行（未安装）时的占位版本
    __version__ = "0.0.0.dev0"

__all__ = [
    "AntiShortcutSkill",
    "PhaseBarrier",
    "StateManager",
    "EvidenceManifest",
    "EvidenceManifestError",
    "EvidenceTamperedError",
    "EVIDENCE_MANIFEST_NAME",
    "SemanticValidator",
    "SemanticCheckResult",
    "RequirementCoverageValidator",
    "MutationScoreValidator",
    "BUILTIN_SEMANTIC_VALIDATORS",
    "SEMANTIC_VALIDATOR_ENTRY_POINT_GROUP",
    "register_semantic_validator",
    "load_semantic_plugins",
    "run_semantic_checks",
    "RemoteAuditSink",
    "GateConfig",
    "STAGES",
    "STAGE_META",
    "classify_stage_path",
    "load_config",
    "bootstrap",
    "install_into",
    "load_plugins",
    "register_integration",
    "LanguageAdapter",
    "PythonAdapter",
    "JavaScriptAdapter",
    "RubyAdapter",
    "CSharpAdapter",
    "CppAdapter",
    "DotNetAdapter",
    "LANGUAGE_REGISTRY",
    "detect_language",
    "get_adapter",
    # v0.12.0：自定义校验器与拦截规则插件 API
    "BUILTIN_VALIDATORS",
    "VALIDATOR_ENTRY_POINT_GROUP",
    "register_validator",
    "load_validator_plugins",
    "get_validator",
    "INTERCEPTOR_ENTRY_POINT_GROUP",
    "register_rule",
    "load_rule_plugins",
    "evaluate_rules",
    # v0.26.0：内置安全规则包
    "BUILTIN_RULES",
    "RULE_DESCRIPTIONS",
    "__version__",
]
