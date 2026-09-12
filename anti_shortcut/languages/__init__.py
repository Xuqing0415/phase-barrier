"""语言适配器注册表与选择逻辑（v0.3.0）。

- ``LANGUAGE_REGISTRY``：内置适配器注册表（名称 -> 适配器类）
- ``detect_language``：按工作区标志文件自动检测语言
- ``get_adapter``：按“显式配置 > 自定义适配器 > 自动检测 > 默认 Python”选择适配器
- ``load_entry_point_adapters``：加载通过 ``phase_barrier.languages`` 入口点注册的第三方适配器
"""
from __future__ import annotations

import importlib
import os
from importlib import metadata
from pathlib import Path
from typing import Any

from ..config import GateConfig
from .base import LanguageAdapter, analyze_js_style_tests, validate_test_collection
from .cpp import CppAdapter
from .dart import DartAdapter
from .csharp import CSharpAdapter
from .dotnet import DotNetAdapter
from .go import GoAdapter
from .php import PhpAdapter
from .java import JavaAdapter
from .kotlin import KotlinAdapter
from .javascript import JavaScriptAdapter
from .python import PythonAdapter
from .ruby import RubyAdapter
from .scala import ScalaAdapter
from .swift import SwiftAdapter
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
    "ScalaAdapter",
    "SwiftAdapter",
    "DotNetAdapter",
    "DartAdapter",
    "detect_language",
    "get_adapter",
    "load_entry_point_adapters",
    "analyze_js_style_tests",
    "validate_test_collection",
]

# 内置适配器注册表：名称 -> 适配器类
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
    "scala": ScalaAdapter,
    "swift": SwiftAdapter,
    "dotnet": DotNetAdapter,
    "kotlin": KotlinAdapter,
    "dart": DartAdapter,
}

# 标志文件 -> 语言（顺序即优先级；同语言内按可依赖程度排序）
_LANGUAGE_MARKERS: list[tuple[tuple[str, ...], str]] = [
    (("package.json",), "javascript"),
    (("pom.xml", "build.gradle", "build.gradle.kts"), "java"),
    (("go.mod",), "go"),
    (("Cargo.toml",), "rust"),
    (("build.sbt",), "scala"),
    (("Package.swift",), "swift"),
    (("pubspec.yaml",), "dart"),
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

# 扩展名 -> 语言（多个标志文件同时命中时用于「文件数消歧」，只覆盖内置适配器）
_EXTENSION_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyw": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".gemspec": "ruby",
    ".cs": "csharp",
    ".csproj": "csharp",
    ".sln": "csharp",
    ".vb": "dotnet",
    ".fs": "dotnet",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".h": "cpp",
    ".c": "cpp",
    ".php": "php",
    ".scala": "scala",
    ".sc": "scala",
    ".swift": "swift",
    ".dart": "dart",
}

# 消歧扫描跳过的目录（依赖 / 构建产物；否则 node_modules 会把任何仓库拉向 javascript）
_COUNT_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", "bower_components", ".venv", "venv",
    "env", "__pycache__", "target", "dist", "build", "out", "vendor",
    "third_party", "site-packages", ".mypy_cache", ".pytest_cache", ".tox",
    ".gradle", ".idea", ".cache", "Pods", "DerivedData", ".phase-barrier-javac",
})

# 消歧扫描的文件数上限：超大仓库只统计前 N 个文件，避免门禁卡在 IO
_COUNT_FILE_LIMIT = 50000


def _count_language_files(root: Path, languages: set[str]) -> dict[str, int]:
    """统计 ``root`` 下各候选语言的文件数（跳过依赖 / 构建目录）。

    只在多个语言的标志文件同时命中时调用，用于消歧。统计到
    ``_COUNT_FILE_LIMIT`` 个文件即停止（遍历顺序不保证），返回已统计结果
    ——足以判断主语言，且不会让门禁卡在超大仓库的 IO 上。
    """
    counts = dict.fromkeys(languages, 0)
    seen = 0
    stack = [root]
    while stack and seen < _COUNT_FILE_LIMIT:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in _COUNT_SKIP_DIRS:
                        stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            seen += 1
            lang = _EXTENSION_LANGUAGE.get(Path(entry.name).suffix.lower())
            if lang in counts:
                counts[lang] += 1
    return counts


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
    v0.62.0：多个语言的标志文件同时命中时（如 django 同时有 ``package.json``
    与 ``setup.py``），按候选语言的源文件数消歧——``package.json`` 常常只是
    前端 / 文档工具链的附属文件，不应把 Python 仓库判定成 javascript。
    """
    root = Path(workspace)
    matched: list[tuple[str, int]] = []
    for order, (markers, lang) in enumerate(_LANGUAGE_MARKERS):
        if any((root / marker).exists() for marker in markers if "*" not in marker):
            matched.append((lang, order))
    for offset, (globs, lang) in enumerate(_LANGUAGE_GLOB_MARKERS):
        if any(any(root.glob(glob)) for glob in globs):
            matched.append((lang, len(_LANGUAGE_MARKERS) + offset))
    # Kotlin 探针：无标志文件的纯 Kotlin 工作区（src/main/kotlin 源根）
    if (root / "src" / "main" / "kotlin").is_dir():
        matched.append(("kotlin", len(_LANGUAGE_MARKERS) + len(_LANGUAGE_GLOB_MARKERS)))
    if not matched:
        return "python"

    languages = {lang for lang, _ in matched}
    if len(languages) > 1:
        counts = _count_language_files(root, languages)
        ranked = sorted(
            languages,
            key=lambda lang: (
                counts.get(lang, 0),
                -min(order for name, order in matched if name == lang),
            ),
            reverse=True,
        )
        best = ranked[0]
        if counts.get(best, 0) > 0:
            return best
    # 只有一个候选语言 / 无法通过文件数消歧（空目录等）：保持原有标志文件优先级
    return matched[0][0]


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
