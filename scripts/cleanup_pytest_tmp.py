#!/usr/bin/env python3
"""清理仓库里的 pytest 临时目录（``.pytest_tmp`` / ``.pytest_cache`` / ``pytest-cache-files-*``）。

背景：本仓库的测试会往 ``.pytest_tmp/`` 写大量临时文件（本轮实测 4.5 万+ 文件、约
480 MB）；Windows 上 ACL 异常会让 pytest 自己的清理静默失败，目录只增不减。
``tests/conftest.py`` 会在 ``%TEMP%\\pytest-of-<user>`` 不可写时把 basetemp 回退到
``.pytest_tmp/tmp-root``，所以该目录在跑测试后重新出现是正常的——本脚本用于**手工**
定期清理，不作为 CI 门禁（``pb-*`` 的 CI 门禁见 ``scripts/cleanup_bench_dirs.py``）。

用法：

    python scripts/cleanup_pytest_tmp.py            # 只扫描（dry-run）
    python scripts/cleanup_pytest_tmp.py --apply    # 真正删除

安全约束：只处理仓库根目录的直接子路径，解析后必须仍在仓库根目录下。
退出码：0 干净 / 已清理；1 有目标且删除失败；2 参数错误。
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Sequence

TARGET_NAMES = (".pytest_tmp", ".pytest_cache")
TARGET_PREFIXES = ("pytest-cache-files-",)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def find_targets(root: Path | None = None) -> list[Path]:
    """返回仓库根目录下需要清理的 pytest 临时目录。"""
    root = (root or repo_root()).resolve()
    targets: list[Path] = []
    for child in sorted(root.iterdir()):
        if not (child.is_dir() or child.is_symlink()):
            continue
        if child.name in TARGET_NAMES or child.name.startswith(TARGET_PREFIXES):
            try:
                if child.resolve().parent == root or child.is_symlink():
                    targets.append(child)
            except OSError:
                continue
    return targets


def _on_error(func, path, exc_info):  # noqa: ANN001 - shutil onerror 签名
    """Windows 只读 / ACL 导致删除失败时先改权限再重试。"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def _remove(path: Path) -> bool:
    """删除目录或符号链接；返回是否已消失。"""
    try:
        if path.is_symlink():
            path.unlink(missing_ok=True)
        else:
            shutil.rmtree(path, onerror=_on_error)
            if path.exists():  # rmtree 有失败时再试一次符号链接删除
                path.unlink(missing_ok=True)
    except OSError:
        pass
    return not path.exists()


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清理仓库里的 pytest 临时目录")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只扫描）")
    args = ap.parse_args(argv)

    targets = find_targets()
    if not targets:
        print("没有需要清理的 pytest 临时目录。")
        return 0

    print(f"发现 {len(targets)} 个 pytest 临时目录：")
    for path in targets:
        print(f"  - {path.name}")
    if not args.apply:
        print("（dry-run）加 --apply 真正删除。")
        return 0

    failures = [path for path in targets if not _remove(path)]
    if failures:
        print(
            "仍有目录无法删除（权限 / 占用）："
            + "、".join(p.name for p in failures),
            file=sys.stderr,
        )
        return 1
    print(f"已删除 {len(targets)} 个目录。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())