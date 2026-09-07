"""scripts/select_swe_tasks.py 与 run_swebench_batch.py 的轻量测试（2026-09）。

选择器：数据清洗、仓库回退、include/exclude、must-repo 配额、轮询数量与确定性。
编排器：仅测纯函数（清单包装解析、label 简化、venv 映射优先级、PB_* 解析），不执行子进程。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEL = ROOT / "scripts" / "select_swe_tasks.py"
BATCH = ROOT / "scripts" / "run_swebench_batch.py"


def _load(script: Path):
    spec = importlib.util.spec_from_file_location(script.stem, script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sel = _load(SEL)
batch = _load(BATCH)


def _row(iid: str, repo: str = "", diff: str = "", **extra) -> dict:
    return {"instance_id": iid, "repo": repo or sel.repo_from_instance_id(iid),
            "patch": "--- a/x\n+++ b/x\n", "base_commit": "a" * 40,
            "difficulty": diff, **extra}


def test_repo_from_instance_id():
    assert sel.repo_from_instance_id("django__django-11039") == "django/django"
    assert sel.repo_from_instance_id("sympy__sympy-11400") == "sympy/sympy"
    assert sel.repo_from_instance_id("marshmallow-code__marshmallow-1343") == \
        "marshmallow-code/marshmallow"


def test_clean_rows_drops_incomplete():
    rows = [_row("a__a-1"), {"instance_id": "b__b-1"}, {"patch": "x"}]
    out = sel.clean_rows(rows)
    assert [r["instance_id"] for r in out] == ["a__a-1"]
    assert out[0]["repo"] == "a/a"


def test_load_rows_wrappers(tmp_path):
    arr = [_row("x__x-1")]
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"instances": arr}), encoding="utf-8")
    assert sel.load_rows(f) == arr
    f2 = tmp_path / "b.json"
    f2.write_text(json.dumps(arr), encoding="utf-8")
    assert sel.load_rows(f2) == arr
    f3 = tmp_path / "bad.json"
    f3.write_text("[]", encoding="utf-8")
    assert sel.load_rows(f3) == []
    f4 = tmp_path / "wrap.json"
    f4.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        sel.load_rows(f4)


def _mixed_rows(n_per_repo: int = 3) -> list[dict]:
    rows: list[dict] = []
    for repo in ("django/django", "sympy/sympy", "psf/requests"):
        for i in range(n_per_repo):
            owner = repo.split("/")[0]
            name = repo.split("/")[1]
            iid = f"{owner}__{name}-{100 + i}"
            rows.append(_row(iid, repo, diff=["easy", "medium", "hard"][i % 3]))
    return rows


def test_select_must_repo_and_count():
    rows = _mixed_rows()
    out = sel.select_tasks(rows, count=8, must_repos=("django/django",),
                           per_repo=2, seed=1)
    assert len(out) == 8
    ids = {r["instance_id"] for r in out}
    assert "django__django-100" in ids and "django__django-101" in ids


def test_select_exclude_and_include():
    rows = _mixed_rows()
    out = sel.select_tasks(rows, count=6, include_repos=("sympy/sympy", "psf/requests"),
                           exclude=("psf__requests-101",), seed=3)
    assert len(out) == 5
    repos = {r["repo"] for r in out}
    assert repos == {"sympy/sympy", "psf/requests"}
    assert "psf__requests-101" not in {r["instance_id"] for r in out}


def test_select_deterministic():
    rows = _mixed_rows(4)
    a = sel.select_tasks(rows, count=9, seed=42)
    b = sel.select_tasks(rows, count=9, seed=42)
    assert [r["instance_id"] for r in a] == [r["instance_id"] for r in b]


def test_select_insufficient_data_warns():
    rows = [_row("django__django-100")]
    out = sel.select_tasks(rows, count=5)
    assert len(out) == 1


def test_batch_load_tasks_wrappers(tmp_path):
    arr = [_row("x__x-1")]
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"tasks": arr}), encoding="utf-8")
    assert batch.load_tasks(f) == arr
    bad = tmp_path / "bad.json"
    bad.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        batch.load_tasks(bad)


def test_batch_short_id_and_venv_precedence():
    assert batch.short_id("marshmallow-code__marshmallow-1343") == \
        "marshmallow-code_marshmallow-1343"
    mapping = {"a__a-1": "va", "a/a": "vr"}
    assert batch.venv_for(mapping, "def", "a__a-1", "a/a") == "va"
    assert batch.venv_for(mapping, "def", "b__b-1", "a/a") == "vr"
    assert batch.venv_for({}, "def", "b__b-1", "z/z") == "def"


def test_batch_parse_pb():
    out = "PB_MODE=gated\nPB_TURNS=27\nPB_RESOLVED=1\njunk\nPB_GATE_FINAL_STAGE=6\n"
    meta = batch.parse_pb(out)
    assert meta["PB_TURNS"] == "27"
    assert meta["PB_RESOLVED"] == "1"
    assert meta["PB_GATE_FINAL_STAGE"] == "6"
    assert "junk" not in meta
