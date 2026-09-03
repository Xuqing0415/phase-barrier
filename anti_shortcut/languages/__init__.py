"""语言适配器注册表与选择逻辑（v0.3.0）。

- ``LANGUAGE_REGISTRY``：内置适配器注册表（名称 → 适配器类）
- ``detect_language``：按工作区标志文件自动检测语言
- ``get_adapter``：按“显式配置 > 自定义适配器 > 自动检测 > 默认 Python”选择适配器
- ``load_entry_point_adapters``：加载通过 ``phase_barrier.languages`` 入口点注册的第三方适配器
"""
from __future__ import annotations

import importlib
from importlib import metadata
from pathlib import Path
from typing import Any

from ..config import GateConfig
from .base import LanguageAdapter, analyze_js_style_tests, validate_test_collection
from .cpp import CppAdapter
from .csharp import CSharpAdapter
from .dotnet import DotNetAdapter
from .go import GoAdapter
from .php import PhpAdapter
from .java import JavaAdapter
from .kotlin import KotlinAdapter
from .javascript import JavaScriptAdapter
from .python import PythonAdapter
from .ruby import RubyAdapter
from .rust import RustAdapter

__all__ = [
    "LANGUAGE_REGISTRY",
    "LanguageAdapter",
    "PythonAdapter",
    "JavaScriptAdapter",
    "JavaAdapter",
    "KotlinAdapter",
    "GoAdapter",
    "RustAdapter",
    "RubyAdapter",
    "CSharpAdapter",
    "CppAdapter",
    "PhpAdapter",
    "DotNetAdapter",
    "detect_language",
    "get_adapter",
    "load_entry_point_adapters",
    "analyze_js_style_tests",
    "validate_test_collection",
]

# 内置适配器注册表：名称 → 适配器类
LANGUAGE_REGISTRY: dict[str, type[LanguageAdapter]] = {
    "python": PythonAdapter,
    "javascript": JavaScriptAdapter,
    "java": JavaAdapter,
    "go": GoAdapter,
    "rust": RustAdapter,
    "ruby": RubyAdapter,
    "csharp": CSharpAdapter,
    "cpp": CppAdapter,
    "php": PhpAdapter,
    "dotnet": DotNetAdapter,
    "kotlin": KotlinAdapter,
}

# 标志文件 → 语言（顺序即优先级；同语言内按可依赖程度排序）
_LANGUAGE_MARKERS: list[tuple[tuple[str, ...], str]] = [
    (("package.json",), "javascript"),
    (("pom.xml", "build.gradle", "build.gradle.kts"), "java"),
    (("go.mod",), "go"),
    (("Cargo.toml",), "rust"),
    (("Gemfile", "Gemfile.lock", ".ruby-version", "Rakefile"), "ruby"),
    (("requirements.txt", "setup.py", "setup.cfg", "tox.ini"), "python"),
    (("pyproject.toml",), "python"),
    (("CMakeLists.txt", "Makefile", "*.vcxproj"), "cpp"),
    (("composer.json",), "php"),
]

# 目录级 glob 标志（如根目录的 *.gemspec / *.csproj / *.sln）
_LANGUAGE_GLOB_MARKERS: list[tuple[tuple[str, ...], str]] = [
    (("*.gemspec",), "ruby"),
    (("*.csproj", "*.sln"), "csharp"),
    (("*.vcxproj",), "cpp"),
]


def load_entry_point_adapters() -> dict[str, type[LanguageAdapter]]:
    """加载通过 ``phase_barrier.languages`` 入口点注册的自定义适配器。

    入口点值可以是适配器类，也可以是返回适配器实例的工厂函数。
    单个入口点加载失败时跳过（不阻断其他适配器）。
    """
    out: dict[str, type[LanguageAdapter]] = {}
    try:
        eps = metadata.entry_points(group="phase_barrier.languages")
    except TypeError:  # Python 3.9- 旧接口（requires-python>=3.10，仅防御）
        eps = metadata.entry_points().get("phase_barrier.languages", [])
    for ep in eps:
        try:
            obj = ep.load()
            cls = obj if isinstance(obj, type) else getattr(obj, "__class__", obj)
            name = getattr(obj, "name", None) or ep.name
            out[str(name)] = obj
        except Exception:
            continue
    return out


def detect_language(workspace: Path) -> str:
    """根据工作区根目录的标志文件自动检测语言；未识别时返回 ``python``（默认）。

    v0.11.0：支持目录级 glob 标志（``*.gemspec`` / ``*.csproj`` / ``*.sln``）。
    """
    root = Path(workspace)
    for markers, lang in _LANGUAGE_MARKERS:
        for marker in markers:
            if (root / marker).exists():
                return lang
    for globs, lang in _LANGUAGE_GLOB_MARKERS:
        for glob in globs:
            if any(root.glob(glob)):
                return lang
    # Kotlin 探针：无标志文件的纯 Kotlin 工作区（src/main/kotlin 源根）
    if (root / "src" / "main" / "kotlin").is_dir():
        return "kotlin"
    return "python"


def get_adapter(
    config: GateConfig | None = None,
    workspace: Path | None = None,
) -> LanguageAdapter:
    """选择语言适配器实例。

    优先级：``config.language`` 显式指定 > ``config.language_adapter`` 自定义导入
    > 工作区自动检测 > 默认 Python。

    :param config: 门禁配置（可为 None，使用默认配置）
    :param workspace: 工作区根目录；为 None 时使用 ``config.workspace``
    :raises ValueError: 显式指定的语言或自定义适配器无法加载时
    """
    cfg = config or GateConfig()
    ws = Path(workspace) if workspace is not None else cfg.workspace

    registry: dict[str, type[LanguageAdapter]] = dict(LANGUAGE_REGISTRY)
    registry.update(load_entry_point_adapters())

    explicit = getattr(cfg, "language", None)
    if explicit:
        cls = registry.get(explicit)
        if cls is None:
            raise ValueError(
                f"未知语言: {explicit!r}，可用适配器: {', '.join(sorted(registry))}"
            )
        return _instantiate(cls, cfg)

    custom = getattr(cfg, "language_adapter", None)
    if custom:
        if custom in registry:
            return _instantiate(registry[custom], cfg)
        return _load_custom_adapter(custom, cfg)

    lang = detect_language(ws)
    cls = registry.get(lang)
    if cls is None:
        cls = PythonAdapter
    return _instantiate(cls, cfg)


def _instantiate(cls: Any, config: GateConfig) -> LanguageAdapter:
    """实例化适配器并传入 ``adapter_options``（兼容类 / 工厂函数）。"""
    options = dict(getattr(config, "adapter_options", None) or {})
    # 兼容三种入口点形式：适配器类 / 返回实例的工厂函数 / 已实例化的适配器
    adapter = cls() if callable(cls) else cls
    configure = getattr(adapter, "configure", None)
    if callable(configure):
        configure(options)
    return adapter


def _load_custom_adapter(import_path: str, config: GateConfig) -> LanguageAdapter:
    """按 ``module.path.ClassName`` 导入自定义适配器。"""
    module_name, sep, attr = import_path.rpartition(".")
    if not sep or not module_name:
        raise ValueError(
            f"language_adapter 必须是可导入的 'module.path.ClassName' 形式: {import_path!r}"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"无法导入 language_adapter 模块 {module_name!r}: {exc}") from exc
    obj = getattr(module, attr, None)
    if obj is None:
        raise ValueError(f"模块 {module_name} 中找不到适配器 {attr!r}")
    return _instantiate(obj, config)
