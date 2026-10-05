"""仓库卫生回归（v1.0.1）：防止临时工作区在仓库根目录无限堆积。

背景：基准 / 演示脚本在系统 Temp 不可写时回退到仓库根目录建临时工作区
（``benchmarks/bench.py`` 等的 ``_make_tmp``），Windows 上 ACL 异常又会让
``shutil.rmtree(..., ignore_errors=True)`` 静默失败，于是 ``pb-*`` 目录越积越多
（本轮实测 2800+ 个）。清理脚本见 ``scripts/cleanup_bench_dirs.py``。

这里只做**门禁**（残留数量超过阈值即失败），不做删除；删除是显式的
``python scripts/cleanup_bench_dirs.py --apply``。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_hygiene_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_no_pb_leftovers_accumulate_in_repo_root():
    mod = _load_script("cleanup_bench_dirs")
    leftovers = mod.find_leftovers(REPO_ROOT)
    assert len(leftovers) <= mod.DEFAULT_MAX_LEFTOVERS, (
        f"仓库根目录积累了 {len(leftovers)} 个 pb-* 临时工作区（阈值 "
        f"{mod.DEFAULT_MAX_LEFTOVERS}）。请运行 "
        "`python scripts/cleanup_bench_dirs.py --apply` 清理，"
        "并检查基准脚本的 temp 回退 / 清理逻辑。"
    )


def test_find_leftovers_only_picks_root_level_pb_dirs(tmp_path):
    mod = _load_script("cleanup_bench_dirs")
    (tmp_path / "pb-bench-exec-1234abcd").mkdir()
    (tmp_path / "pb-fuzz-http-deadbeef").mkdir()
    (tmp_path / "keep-me").mkdir()
    (tmp_path / "notpb").mkdir()
    (tmp_path / "pb-not-a-dir.txt").write_text("x", encoding="utf-8")
    (tmp_path / "keep-me" / "pb-nested").mkdir()

    names = {p.name for p in mod.find_leftovers(tmp_path)}
    assert names == {"pb-bench-exec-1234abcd", "pb-fuzz-http-deadbeef"}


def test_cleanup_pytest_tmp_finds_only_root_level_targets(tmp_path):
    mod = _load_script("cleanup_pytest_tmp")
    for name in (".pytest_tmp", ".pytest_cache", "pytest-cache-files-abc"):
        (tmp_path / name).mkdir()
    (tmp_path / "unrelated").mkdir()
    (tmp_path / "unrelated" / ".pytest_tmp").mkdir()
    (tmp_path / ".pytest_tmp" / "nested").mkdir()

    names = {p.name for p in mod.find_targets(tmp_path)}
    assert names == {".pytest_tmp", ".pytest_cache", "pytest-cache-files-abc"}


def test_cleanup_scripts_do_not_delete_outside_repo_root(tmp_path):
    """安全约束：find_* 只返回传入根目录的直接子路径。"""
    bench = _load_script("cleanup_bench_dirs")
    pytest_tmp = _load_script("cleanup_pytest_tmp")
    (tmp_path / "pb-x").mkdir()
    (tmp_path / ".pytest_tmp").mkdir()
    for mod, finder in ((bench, bench.find_leftovers), (pytest_tmp, pytest_tmp.find_targets)):
        for path in finder(tmp_path):
            assert path.parent == tmp_path, f"{mod.__name__} 返回了根目录之外的路径：{path}"