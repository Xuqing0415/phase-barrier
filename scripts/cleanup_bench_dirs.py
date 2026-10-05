#!/usr/bin/env python3
"""清理仓库根目录下遗留的临时基准 / 演示工作区（``pb-*``）。

背景：``benchmarks/*.py``、``examples/**``、``benchmarks/red_team`` 都用
``tempfile.mkdtemp`` 建临时工作区；当系统 Temp 不可写时（例如 Windows 上
``%TEMP%\\pytest-of-<user>`` 的 ACL 异常），``_make_tmp`` 会回退到仓库根目录，而
``shutil.rmtree(..., ignore_errors=True)`` 又会把 ACL 失败静默吞掉，于是这些目录越积越多
（本轮实测 2800+ 个）。

用法：

    python scripts/cleanup_bench_dirs.py            # 只扫描（dry-run）
    python scripts/cleanup_bench_dirs.py --apply    # 真正删除

安全约束（宁可漏删，不可误删）：

- 只处理**仓库根目录的直接子目录**，且名字匹配 ``pb-*``；
- 目录内**不能有任何 git 跟踪文件**；
- 解析后的路径必须仍在仓库根目录下。

退出码：0 干净 / 清理完成；1 存在残留且未 ``--apply``（CI 门禁用）；2 参数错误。
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Sequence

#: 允许的残留数量：超过即视为「需要清理」（CI 门禁）
DEFAULT_MAX_LEFTOVERS = 10


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _tracked_top_level(root: Path) -> set[str]:
    """一次性取回所有 git 跟踪文件的一级目录名（避免逐目录起 git 进程）。"""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    names: set[str] = set()
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line:
            names.add(line.split("/", 1)[0])
    return names


def find_leftovers(root: Path | None = None) -> list[Path]:
    """返回仓库根目录下可安全删除的 ``pb-*`` 残留目录（按名字排序）。"""
    root = (root or repo_root()).resolve()
    tracked = _tracked_top_level(root)
    found: list[Path] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not child.name.startswith("pb-"):
            continue
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if resolved.parent != root:  # 防御：符号链接指向别处
            continue
        if child.name in tracked:  # 防御：万一将来有同名被跟踪目录
            continue
        found.append(child)
    return found


def _on_error(func, path, exc_info):  # noqa: ANN001 - shutil onerror 签名
    """rmtree 的兜底：Windows 只读 / ACL 导致删除失败时先改权限再重试。"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_leftovers(paths: Iterable[Path]) -> list[Path]:
    """逐个删除；返回仍未能删除的路径。"""
    failures: list[Path] = []
    for path in paths:
        shutil.rmtree(path, onerror=_on_error)
        if path.exists():
            failures.append(path)
    return failures


def _dir_size(paths: Iterable[Path], limit: int = 200) -> tuple[int, bool]:
    """粗略统计体积；目录数超过 limit 时跳过（避免扫描过慢）。"""
    paths = list(paths)
    if len(paths) > limit:
        return 0, False
    total = 0
    for path in paths:
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in filenames:
                try:
                    total += (Path(dirpath) / name).stat().st_size
                except OSError:
                    pass
    return total, True


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清理仓库根目录下遗留的 pb-* 临时工作区")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只扫描）")
    ap.add_argument(
        "--max",
        type=int,
        default=DEFAULT_MAX_LEFTOVERS,
        help=f"允许的残留数量，超过即失败（默认 {DEFAULT_MAX_LEFTOVERS}）",
    )
    args = ap.parse_args(argv)

    root = repo_root()
    leftovers = find_leftovers(root)
    if not leftovers:
        print("仓库根目录没有 pb-* 残留目录。")
        return 0

    total_bytes, measured = _dir_size(leftovers)
    size_note = f"，约 {total_bytes / 1024 / 1024:.1f} MB" if measured else ""
    print(f"发现 {len(leftovers)} 个 pb-* 残留目录{size_note}：")
    for path in leftovers[:20]:
        print(f"  - {path.name}")
    if len(leftovers) > 20:
        print(f"  ...（其余 {len(leftovers) - 20} 个略）")

    if not args.apply:
        if len(leftovers) > args.max:
            print(
                f"残留数 {len(leftovers)} 超过阈值 {args.max}：请运行 "
                "`python scripts/cleanup_bench_dirs.py --apply` 清理。",
                file=sys.stderr,
            )
            return 1
        return 0

    failures = remove_leftovers(leftovers)
    if failures:
        print(
            f"仍有 {len(failures)} 个目录无法删除（可能是权限 / 占用）："
            + "、".join(p.name for p in failures[:10]),
            file=sys.stderr,
        )
        return 1
    print(f"已删除 {len(leftovers)} 个目录。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())