"""scripts/select_swe_tasks.py 与 run_swebench_batch.py 的轻量测试（2026-09）。

选择器：数据清洗、仓库回退、include/exclude、must-repo 配额、轮询数量与确定性。
编排器：仅测纯函数（清单包装解析、label 简化、venv 映射优先级、PB_* 解析），不执行子进程。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEL = ROOT / "scripts" / "select_swe_tasks.py"
BATCH = ROOT / "scripts" / "run_swebench_batch.py"
CONTAINER = ROOT / "scripts" / "run_swebench_batch_container.py"


def _load(script: Path):
    spec = importlib.util.spec_from_file_location(script.stem, script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sel = _load(SEL)
batch = _load(BATCH)
container = _load(CONTAINER)


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
    f5 = tmp_path / "tasks.json"
    f5.write_text(json.dumps({"tasks": arr}), encoding="utf-8")
    assert sel.load_rows(f5) == arr


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



def test_container_image_prefers_dataset_field():
    assert container.container_image(
        {"instance_id": "psf__requests-1963", "image": "swebench/x:latest"}) == "swebench/x:latest"
    assert container.container_image({"instance_id": "astropy__astropy-12907"}) == \
        "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"


def test_container_path_translation(tmp_path):
    sub = tmp_path / "a" / "b.json"
    sub.parent.mkdir(parents=True)
    sub.write_text("{}", encoding="utf-8")
    assert container.container_path(sub, tmp_path) == "/pb/a/b.json"
    assert container.container_path(sub, tmp_path, mount_point="/mnt/repo") == "/mnt/repo/a/b.json"
    with pytest.raises(ValueError):
        container.container_path(tmp_path.parent / "outside.json", tmp_path)


def test_build_agent_docker_command_shape(tmp_path):
    argv = container.build_agent_docker_command(
        image="swebench/sweb.eval.x86_64.x_1776_x-1:latest", repo_mount=tmp_path,
        agent_script="/pb/bench/run_agent.py", dataset="/pb/bench/ds.json",
        instance_id="x__x-1", mode="gated", label="x_x-1_gated", outdir="/pb/bench/runs",
        testbed_python="/opt/miniconda3/envs/testbed/bin/python",
        gate_python="/opt/miniconda3/bin/python", max_turns=60, exec_timeout=300,
        extra_env={"DEEPSEEK_API_KEY": "k", "PB_DS_MODEL": "deepseek-v4-pro"})
    assert argv[0] == "docker" and argv[1] == "run" and "--rm" in argv
    assert f"{tmp_path}:/pb" in argv
    assert "/testbed" in argv and "--entrypoint" in argv
    assert "/opt/miniconda3/bin/python /pb/bench/run_agent.py" in argv[-1]
    assert "--venv /opt/miniconda3/envs/testbed/bin/python" in argv[-1]
    assert "--mode gated" in argv[-1] and "--max-turns 60" in argv[-1]
    assert "DEEPSEEK_API_KEY=k" in argv and "PB_DS_MODEL=deepseek-v4-pro" in argv


def test_pending_jobs_skips_recorded():
    tasks = [{"instance_id": "a__a-1", "repo": "a/a"}, {"instance_id": "b__b-1", "repo": "b/b"}]
    done = {("a__a-1", "baseline")}
    jobs = container.pending_jobs(tasks, done, ["baseline", "gated"])
    assert [(j["instance_id"], j["mode"]) for j in jobs] == \
        [("a__a-1", "gated"), ("b__b-1", "baseline"), ("b__b-1", "gated")]


def test_container_parse_pb_and_short_id():
    assert container.short_id("pylint-dev__pylint-5859") == "pylint-dev_pylint-5859"
    meta = container.parse_pb("PB_MODE=gated\nPB_GATE_COMPLETED=1\nnoise")
    assert meta == {"PB_MODE": "gated", "PB_GATE_COMPLETED": "1"}

# ---------- --balance-difficulty（2026-11） ----------


def _repo_rows(repo: str, diffs: list[str]) -> list[dict]:
    owner, name = repo.split("/")
    return [
        _row(f"{owner}__{name}-{100 + i}", repo, diff=diff)
        for i, diff in enumerate(diffs)
    ]


def test_quota_by_repo_largest_remainder():
    pools = {
        "django/django": [{}] * 10,
        "sympy/sympy": [{}] * 5,
        "pallets/flask": [{}] * 1,
    }
    quota = sel.quota_by_repo(pools, 5)
    assert quota == {"django/django": 2, "pallets/flask": 1, "sympy/sympy": 2}
    assert sum(quota.values()) == 5


def test_quota_by_repo_count_less_than_repo_count():
    pools = {f"o/r{i}": [{}] * (i + 1) for i in range(3)}
    quota = sel.quota_by_repo(pools, 2)
    assert sum(quota.values()) == 2
    assert quota["o/r2"] == 1 and quota["o/r1"] == 1 and quota["o/r0"] == 0


def test_quota_by_repo_caps_at_available():
    quota = sel.quota_by_repo({"a/a": [{}] * 2, "b/b": [{}] * 1}, 10)
    assert quota == {"a/a": 2, "b/b": 1}


def test_rotate_by_difficulty_round_robin():
    rows = _repo_rows("django/django", ["hard", "easy", "medium", "hard", "easy"])
    out = sel.rotate_by_difficulty(rows, quota=4, seed=7, repo="django/django")
    levels = ["easy", "medium", "hard"]
    start = int.from_bytes(
        hashlib.sha256(b"7:django/django").digest()[:4], "big"
    ) % len(levels)
    expected = levels[start:] + levels[:start]
    assert [r["difficulty"] for r in out[:3]] == expected
    assert len(out) == 4 and {r["difficulty"] for r in out} == set(levels)


def test_rotate_by_difficulty_verified_time_buckets():
    rows = _repo_rows(
        "x/x", ["15 min - 1 hour", "<15 min fix", "1-4 hours", ">4 hours"]
    )
    out = sel.rotate_by_difficulty(rows, quota=4, seed=1, repo="x/x")
    assert {r["difficulty"] for r in out} == {
        "<15 min fix",
        "15 min - 1 hour",
        "1-4 hours",
        ">4 hours",
    }


def test_balanced_selection_meta_and_min_one_repo():
    rows: list[dict] = []
    for repo, n in [
        ("django/django", 20),
        ("sympy/sympy", 8),
        ("sphinx-doc/sphinx", 5),
        ("pallets/flask", 1),
        ("psf/requests", 3),
    ]:
        rows += _repo_rows(
            repo, ["<15 min fix", "15 min - 1 hour", "1-4 hours"] * 10
        )[:n]

    chosen, meta = sel.select_tasks_balanced(rows, count=10, seed=7)
    assert len(chosen) == 10
    assert {r["repo"] for r in chosen} == {r["repo"] for r in rows}
    assert all(info["quota"] >= 1 for info in meta["repos"].values())
    assert sum(info["quota"] for info in meta["repos"].values()) == 10
    assert all(info["selected"] == info["quota"] for info in meta["repos"].values())
    assert meta["count_selected"] == 10
    assert meta["difficulty_distribution"] == meta["difficulty_selected"]
    assert sum(meta["difficulty_distribution"].values()) == 10


def test_balanced_selection_deterministic():
    rows = _mixed_rows(4)
    first, first_meta = sel.select_tasks_balanced(rows, count=9, seed=42)
    second, second_meta = sel.select_tasks_balanced(rows, count=9, seed=42)
    assert [r["instance_id"] for r in first] == [r["instance_id"] for r in second]
    assert first_meta == second_meta


def test_balanced_flag_off_is_backward_compatible():
    rows = _mixed_rows(4)
    old = sel.select_tasks(rows, count=9, seed=42)
    new = sel.select_tasks(rows, count=9, seed=42, balance_difficulty=False)
    assert [r["instance_id"] for r in old] == [r["instance_id"] for r in new]


def test_balanced_selection_records_skewed_repo_note():
    rows = _repo_rows("only/medium", ["medium"] * 3) + _repo_rows(
        "other/other", ["easy", "hard"]
    )
    _chosen, meta = sel.select_tasks_balanced(rows, count=3, seed=7)
    info = meta["repos"]["only/medium"]
    assert info["quota"] == 2
    assert info["balance_note"]


def test_cli_balance_difficulty_writes_meta(tmp_path):
    rows = _mixed_rows(3)
    src = tmp_path / "dataset.json"
    src.write_text(json.dumps(rows), encoding="utf-8")
    out = tmp_path / "out.json"
    assert sel.main(
        [
            "--dataset",
            str(src),
            "--count",
            "5",
            "--balance-difficulty",
            "--seed",
            "7",
            "--out",
            str(out),
        ]
    ) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["tasks"]) == 5
    assert payload["selection_meta"]["balance_difficulty"] is True
    assert payload["selection_meta"]["count_selected"] == 5
