"""执行前后工作区快照与「未授权写入」检测（v1.0.1，纵深防御 2-B 的检测版）。

写路径提取（``extract_written_paths``）是启发式的：它认识重定向、``mv``/``cp``、
``sed -i``、``tee``、``dd of=``、``rm``/``touch``、脚本解释器里的 ``open(...,'w')``
等常见形态。但 shell 的写文件方式无穷无尽（``ruby -e``、PowerShell、``awk``、
自己编译的小程序、自定义构建脚本……），只靠正则穷举，每发现一种新变体就要补一个洞。

本模块提供**事后检测**兜底：``execute_command`` 执行前后各取一次工作区快照
（只记路径 + mtime + size，不复制内容），对比出新增 / 修改 / 删除的文件。凡是没有经过
``write_file`` 记录的变更，都记为「未授权写入」写进审计日志。

v1.0.1 只做**检测**（不拦截、不回退）：先把真实漏检暴露出来、收集误报率，再决定是否加
回退。这样既补齐「启发式漏检」的盲区，又不引入误伤。
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

#: 默认跳过的目录名（支持 fnmatch 通配）；构建产物 / 依赖 / 版本控制 / 门禁自身
DEFAULT_EXCLUDED_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    ".agent_gate",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "pytest-cache-files-*",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "dist",
    "build",
    "site-packages",
    "vendor",
    "target",
)

#: 默认跳过的文件名（支持 fnmatch 通配）：覆盖率等工具产物
DEFAULT_EXCLUDED_FILES: tuple[str, ...] = (
    ".coverage",
    ".coverage.*",
    "coverage.xml",
    "coverage.json",
)

#: 默认跳过的后缀：字节码等纯编译产物
DEFAULT_EXCLUDED_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".pyd")

#: 快照：相对路径（posix 风格）-> (mtime_ns, size)
Snapshot = dict[str, tuple[int, int]]


@dataclass(frozen=True)
class FileChange:
    """快照差异里的一处变更。"""

    path: str
    #: ``created`` / ``modified`` / ``deleted``
    change: str


def _matches_any(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def iter_workspace_files(
    root: str | Path,
    *,
    excluded_dirs: Iterable[str] = DEFAULT_EXCLUDED_DIRS,
    excluded_files: Iterable[str] = DEFAULT_EXCLUDED_FILES,
    excluded_suffixes: Iterable[str] = DEFAULT_EXCLUDED_SUFFIXES,
) -> Iterator[tuple[str, Path]]:
    """遍历工作区文件，跳过排除目录 / 文件；产出 ``(相对 posix 路径, 绝对路径)``。"""
    root_path = Path(root)
    dir_patterns = tuple(excluded_dirs)
    file_patterns = tuple(excluded_files)
    suffixes = tuple(excluded_suffixes)
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if not _matches_any(d, dir_patterns)]
        for name in filenames:
            if _matches_any(name, file_patterns) or name.endswith(suffixes):
                continue
            full = Path(dirpath) / name
            try:
                rel = full.relative_to(root_path).as_posix()
            except ValueError:  # pragma: no cover - os.walk 不会给出根之外的路径
                continue
            yield rel, full


def snapshot_workspace(
    root: str | Path,
    *,
    excluded_dirs: Iterable[str] = DEFAULT_EXCLUDED_DIRS,
    excluded_files: Iterable[str] = DEFAULT_EXCLUDED_FILES,
    excluded_suffixes: Iterable[str] = DEFAULT_EXCLUDED_SUFFIXES,
) -> Snapshot:
    """取工作区快照（只记路径 + mtime + size，不读内容）。"""
    snap: Snapshot = {}
    for rel, full in iter_workspace_files(
        root,
        excluded_dirs=excluded_dirs,
        excluded_files=excluded_files,
        excluded_suffixes=excluded_suffixes,
    ):
        try:
            st = full.stat()
        except OSError:
            continue
        snap[rel] = (st.st_mtime_ns, st.st_size)
    return snap


def diff_snapshots(before: Snapshot, after: Snapshot) -> list[FileChange]:
    """对比两次快照，返回按路径排序的变更列表。"""
    changes: list[FileChange] = []
    for path, meta in after.items():
        if path not in before:
            changes.append(FileChange(path, "created"))
        elif before[path] != meta:
            changes.append(FileChange(path, "modified"))
    for path in before:
        if path not in after:
            changes.append(FileChange(path, "deleted"))
    changes.sort(key=lambda c: (c.path, c.change))
    return changes