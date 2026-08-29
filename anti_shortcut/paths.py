"""路径工具：glob 模式匹配、文件遍历、哈希等与语言无关的基础工具。

从 ``validators.py`` 拆出，供语言适配器与校验器共用，避免循环导入。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import GateConfig


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _glob_part_to_regex(part: str) -> str:
    """把 glob 片段转换为正则（* 不跨目录，? 匹配单字符）。"""
    out: list[str] = []
    for ch in part:
        if ch == "*":
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _pattern_to_regex(pattern: str) -> re.Pattern:
    """把 glob 模式转换为正则，支持 ``**``（零个或多个目录层级）。"""
    parts = pattern.replace("\\", "/").split("/")
    out: list[str] = []
    prev_dstar = False
    n = len(parts)
    for i, part in enumerate(parts):
        if part == "":
            continue
        if part == "**":
            if out:
                out.append("/")
            out.append("(?:[^/]+(?:/[^/]+)*/)?" if i < n - 1 else "(?:[^/]+(?:/[^/]+)*)?")
            prev_dstar = True
        else:
            if out and not prev_dstar:
                out.append("/")
            out.append(_glob_part_to_regex(part))
            prev_dstar = False
    return re.compile("^" + "".join(out) + "$")


def path_matches(path: Path, patterns: list[str]) -> bool:
    """判断路径是否匹配任意 glob 模式（支持 ``**`` 递归目录）。

    匹配候选包括：完整路径、文件名、以及路径的每个尾缀（目录级模式在
    绝对路径 / 相对路径下都能命中，例如 ``src/**/*.ts`` 可匹配
    ``D:/ws/src/fib.ts`` 的尾缀 ``src/fib.ts``）。
    """
    posix = path.as_posix()
    parts = posix.split("/")
    candidates = {posix, path.name, *("/".join(parts[i:]) for i in range(len(parts)))}
    for pattern in patterns:
        regex = _pattern_to_regex(pattern)
        for candidate in candidates:
            if regex.match(candidate):
                return True
    return False


def classify_path(path: str | Path, config: GateConfig) -> str:
    """把路径分类为 test / source / other。"""
    p = Path(path)
    if path_matches(p, config.test_file_patterns):
        return "test"
    if path_matches(p, config.source_file_patterns):
        return "source"
    return "other"


def iter_workspace_files(workspace: Path, config: GateConfig) -> list[Path]:
    """遍历工作区文件，跳过门禁目录与常见无关目录。"""
    skip = {config.gate_dir_name, ".git", "__pycache__", ".venv", "venv", "node_modules"}
    out: list[Path] = []
    for p in workspace.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(workspace).parts
        if any(part in skip for part in rel_parts):
            continue
        out.append(p)
    return sorted(out)
