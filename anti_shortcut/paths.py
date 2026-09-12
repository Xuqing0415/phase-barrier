"""路径工具：glob 模式匹配、文件遍历、哈希等与语言无关的基础工具。

从 ``validators.py`` 拆出，供语言适配器与校验器共用，避免循环导入。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
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
    skip = {config.gate_dir_name, ".git", "__pycache__", ".venv", "venv", "node_modules", "target", ".phase-barrier-javac", ".build"}  # SwiftPM 构建产物
    out: list[Path] = []
    for p in workspace.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(workspace).parts
        if any(part in skip for part in rel_parts):
            continue
        out.append(p)
    return sorted(out)


def changed_workspace_files(workspace: Path) -> list[Path] | None:
    """返回工作区「本次变更」的文件（Git 相对 HEAD 的改动 + 未跟踪文件）。

    只在 ``workspace`` 本身就是 Git 仓库根目录时启用，避免在子目录里把上层
    仓库的变更误当成工作区变更。返回 ``None`` 表示无法判断（非 Git 仓库 /
    git 不可用 / 工作区不是仓库根），调用方应退回「扫描整个工作区」；返回
    空列表表示仓库干净、没有未提交变更。
    """
    ws = Path(workspace).resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(ws), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0 or not (top.stdout or "").strip():
        return None
    try:
        if Path(top.stdout.strip()).resolve() != ws:
            return None
    except OSError:
        return None
    try:
        status = subprocess.run(
            ["git", "-C", str(ws), "status", "--porcelain", "-z", "--untracked-files=all"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if status.returncode != 0:
        return None

    out: list[Path] = []
    chunks = (status.stdout or "").split("\0")
    index = 0
    while index < len(chunks):
        chunk = chunks[index]
        index += 1
        if len(chunk) < 4:
            continue
        state, rel = chunk[:2], chunk[3:]
        if state[0] in ("R", "C"):
            index += 1  # porcelain -z：重命名 / 复制会额外跟一个「源路径」字段
        out.append(ws / rel)
    return out


def norm_path_key(path: Path) -> str:
    """路径比较键：绝对化 + 平台大小写归一（Windows 下不区分大小写）。"""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = Path(os.path.abspath(path))
    return os.path.normcase(str(resolved))
