"""插件发现与自动验证（v0.29.0）。

phase-barrier 支持五类插件入口点（entry points）：

- ``phase_barrier.languages``：自定义语言适配器
- ``phase_barrier.validators``：自定义阶段校验器（覆盖内置）
- ``phase_barrier.interceptors``：自定义拦截规则
- ``phase_barrier.semantic_validators``：语义级校验器（v0.49.0，需求追踪 /
  变异测试 / LLM 审查等，见 ``anti_shortcut.semantic``）
- ``anti_shortcut.integrations``：Agent 集成插件（自动装回包装后的工具）

``verify_plugins()`` 对当前环境已安装的全部插件做冒烟验证（加载、类型、
必需接口检查），供插件开发者 CI 与 ``plugin-verify`` CLI 使用：

    python -m anti_shortcut plugin-verify --json
"""
from __future__ import annotations

from importlib import metadata
from typing import Any

__all__ = [
    "PLUGIN_GROUPS",
    "discover_plugins",
    "verify_plugins",
    "verify_language_adapter",
]

PLUGIN_GROUPS: tuple[str, ...] = (
    "phase_barrier.languages",
    "phase_barrier.validators",
    "phase_barrier.interceptors",
    "phase_barrier.semantic_validators",
    "anti_shortcut.integrations",
)

# 语言适配器必须实现的方法（LanguageAdapter 抽象接口）
_LANGUAGE_REQUIRED_METHODS: tuple[str, ...] = (
    "check_syntax",
    "analyze_tests",
    "is_source_file",
    "is_test_file",
    "identify_test_command",
    "parse_test_output",
)


def _entry_points(group: str) -> list[Any]:
    try:
        return list(metadata.entry_points(group=group))
    except TypeError:  # Python 3.9- 旧接口（requires-python>=3.10，仅防御）
        return list(metadata.entry_points().get(group, []))


def discover_plugins() -> dict[str, list[dict[str, str]]]:
    """列出当前环境已注册的全部插件：{group: [{"name": ..., "value": ...}]}。"""
    out: dict[str, list[dict[str, str]]] = {}
    for group in PLUGIN_GROUPS:
        items = []
        for ep in _entry_points(group):
            items.append({"name": ep.name, "value": ep.value})
        if items:
            out[group] = items
    return out


def verify_language_adapter(obj: Any) -> list[str]:
    """校验语言适配器对象/类，返回错误信息列表（空列表表示通过）。"""
    errors: list[str] = []
    cls = obj if isinstance(obj, type) else obj.__class__
    name = getattr(obj, "name", None) or getattr(cls, "name", "")
    if not isinstance(name, str) or not name.strip():
        errors.append("name 必须是非空字符串（语言标识，如 'my_language'）")
    for method in _LANGUAGE_REQUIRED_METHODS:
        fn = getattr(obj, method, None)
        if not callable(fn):
            errors.append(f"缺少可调用的 {method}() 方法")
    return errors


def _verify_entry(group: str, ep: Any) -> tuple[bool, list[str]]:
    """验证单个入口点；返回 (是否通过, 错误列表)。"""
    try:
        obj = ep.load()
    except Exception as exc:  # 导入失败：给出明确错误而非静默跳过
        return False, [f"无法加载入口点: {exc.__class__.__name__}: {exc}"]
    errors: list[str] = []
    if group == "phase_barrier.languages":
        errors.extend(verify_language_adapter(obj))
    elif group == "phase_barrier.validators":
        # 与 validators._coerce_validator_mapping 兼容：{stage: fn} / 带 stage 的可调用 / 工厂
        if isinstance(obj, dict):
            bad = [k for k, v in obj.items() if not callable(v)]
            if bad:
                errors.append(f"validators 映射存在不可调用值: {bad}")
            if not obj:
                errors.append("validators 映射为空")
        elif callable(obj):
            if getattr(obj, "stage", None) is None:
                try:
                    mapping = obj()
                except Exception as exc:
                    errors.append(f"工厂调用失败: {exc.__class__.__name__}: {exc}")
                else:
                    if not isinstance(mapping, dict) or not mapping:
                        errors.append("工厂必须返回非空 {stage: validator} 映射")
                    else:
                        bad = [k for k, v in mapping.items() if not callable(v)]
                        if bad:
                            errors.append(f"工厂返回的映射存在不可调用值: {bad}")
        else:
            errors.append("校验器必须是 {stage: fn} 映射、带 stage 的可调用或返回映射的工厂")
    elif group == "phase_barrier.interceptors":
        rules = getattr(obj, "rules", None)
        candidates: list[Any] = []
        if rules is not None:
            candidates = rules if isinstance(rules, (list, tuple)) else []
        elif isinstance(obj, dict):
            candidates = list(obj.values())
        elif isinstance(obj, (list, tuple)):
            candidates = list(obj)
        elif callable(obj):
            try:
                result = obj()
            except TypeError:
                candidates = [obj]
            else:
                candidates = result if isinstance(result, (list, tuple)) else [obj]
        callable_rules = [r for r in candidates if callable(r)]
        if not callable_rules:
            errors.append("拦截规则入口点未提供任何可调用规则（规则函数 / 规则列表 / 工厂）")
    elif group == "phase_barrier.semantic_validators":
        # 语义校验器契约（v0.49.0）：name + stages + check()
        if isinstance(obj, type):
            try:
                obj = obj()
            except Exception as exc:
                errors.append(f"语义校验器类实例化失败: {exc.__class__.__name__}: {exc}")
        name = getattr(obj, "name", None)
        if not isinstance(name, str) or not name.strip():
            errors.append("语义校验器必须提供非空 name 字符串")
        stages = getattr(obj, "stages", None)
        if not stages or not all(
            isinstance(s, int) and not isinstance(s, bool) and 0 <= s <= 6 for s in stages
        ):
            errors.append("语义校验器 stages 必须是非空 0-6 整数列表（在哪些当前阶段推进时运行）")
        if not callable(getattr(obj, "check", None)):
            errors.append("语义校验器必须提供 check(workspace, config, state, adapter=None) 方法")
    elif group == "anti_shortcut.integrations":
        # 集成插件：可调用（如安装函数）或提供 install 方法
        install = getattr(obj, "install", None)
        if not callable(obj) and not callable(install):
            errors.append("集成插件必须是可调用对象或提供 install() 方法")
    return (not errors), errors


def verify_plugins() -> dict[str, dict[str, dict[str, Any]]]:
    """验证当前环境全部插件。

    返回结构::

        {
          "phase_barrier.languages": {
            "my_language": {"ok": True, "errors": []},
            "broken": {"ok": False, "errors": ["..."]},
          },
          ...
        }
    """
    results: dict[str, dict[str, dict[str, Any]]] = {}
    for group in PLUGIN_GROUPS:
        group_results: dict[str, dict[str, Any]] = {}
        for ep in _entry_points(group):
            ok, errors = _verify_entry(group, ep)
            group_results[ep.name] = {"ok": ok, "errors": errors}
        if group_results:
            results[group] = group_results
    return results


def summarize_plugin_verification(results: dict[str, dict[str, dict[str, Any]]]) -> tuple[bool, str]:
    """把 verify_plugins() 结果规整为 (是否全部通过, 人类可读摘要)。"""
    failed: list[str] = []
    total = 0
    for group, plugins in results.items():
        for name, info in plugins.items():
            total += 1
            if not info["ok"]:
                failed.append(f"{group}:{name} -> " + "；".join(info["errors"]))
    if not failed:
        return True, f"全部 {total} 个插件验证通过"
    return False, f"{len(failed)}/{total} 个插件验证失败:\n" + "\n".join(failed)
