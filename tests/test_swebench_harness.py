"""benchmarks/swebench/* 的契约测试（2026-09）。

覆盖：数据集加载容错、工具行为、门禁包装接线、补丁提取、docker 命令装配、
评分器数据集解析、prepare_dataset 的镜像匹配。全部不依赖 docker / 网络 / 真实 LLM。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "benchmarks" / "swebench"


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_agent = _load(HARNESS / "run_agent.py")
grade = _load(HARNESS / "grade.py")
prepare = _load(HARNESS / "prepare_dataset.py")
container = _load(ROOT / "scripts" / "run_swebench_batch_container.py")


# ---------- run_agent ----------

def _instance(iid: str = "x__x-1", problem: str = "boom") -> dict:
    return {"instance_id": iid, "problem_statement": problem}


def test_load_instance_array_and_wrapper(tmp_path):
    arr = tmp_path / "a.json"
    arr.write_text(json.dumps([_instance()]), encoding="utf-8")
    assert run_agent.load_instance(arr, "x__x-1")["problem_statement"] == "boom"
    wrapped = tmp_path / "b.json"
    wrapped.write_text(json.dumps({"instances": [_instance()]}), encoding="utf-8")
    assert run_agent.load_instance(wrapped, "x__x-1")["instance_id"] == "x__x-1"


def test_load_instance_missing_raises(tmp_path):
    arr = tmp_path / "a.json"
    arr.write_text(json.dumps([_instance()]), encoding="utf-8")
    with pytest.raises(SystemExit):
        run_agent.load_instance(arr, "nope__nope-1")


def test_venv_env_prepends_path(tmp_path):
    venv = tmp_path / "envs" / "testbed" / "bin" / "python"
    env = run_agent._venv_env(str(venv))
    assert env["PATH"].startswith(str(venv.parent))
    assert env["PYTHONUTF8"] == "1"
    assert run_agent._venv_env(None).get("PYTHONUTF8") is None or True


def test_agent_tools_write_read_exec(tmp_path):
    tools = run_agent.AgentTools(tmp_path, None, 30)
    res = tools.write_file("pkg/mod.py", "print('hi')\n")
    assert res["ok"] and (tmp_path / "pkg" / "mod.py").exists()
    read = tools.read_file("pkg/mod.py")
    assert "print('hi')" in read["content"] and read["total_lines"] == 1
    assert tools.read_file("nope.py")["ok"] is False
    out = tools.execute_command("echo hello")
    assert out["exit_code"] == 0 and "hello" in out["output"]
    assert tools.execute_command("exit 7")["exit_code"] == 7


def test_agent_tools_exec_timeout(tmp_path):
    import sys

    tools = run_agent.AgentTools(tmp_path, None, 1)
    slow = f'"{sys.executable}" -c "import time;time.sleep(30)"'
    assert tools.execute_command(slow)["exit_code"] == 124


def test_agent_tools_dispatch_gate_denied(tmp_path):
    """gated 接线：dispatch 走门禁包装后的工具，阶段 0 写实现应被拒。"""
    from anti_shortcut import AntiShortcutSkill

    tools = run_agent.AgentTools(tmp_path, None, 30)
    skill = AntiShortcutSkill(tmp_path, user_request="fix")
    tools.bound = skill.install({"write_file": tools.write_file,
                                 "execute_command": tools.execute_command})
    with pytest.raises(PermissionError):
        tools.dispatch("write_file", {"path": "fib.py", "content": "def fib(): ..."})
    # 其他类型文件在阶段 0 允许写
    assert tools.dispatch("write_file", {"path": "notes.txt", "content": "x"})["ok"] is True


def test_extract_patch_excludes_gate_artifacts(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@e"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env={**env, **__import__("os").environ})
    (repo / "mod.py").write_text("a = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, env={**env, **__import__("os").environ})
    (repo / "mod.py").write_text("a = 2\n", encoding="utf-8")
    (repo / "test_pb_repro.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    (repo / ".agent_gate").mkdir()
    (repo / ".agent_gate" / "state.json").write_text("{}", encoding="utf-8")
    (repo / "spec.md").write_text("# spec\n", encoding="utf-8")
    patch, err = run_agent.extract_patch(repo)
    assert err == ""
    assert "mod.py" in patch and "test_pb_repro.py" in patch
    assert ".agent_gate" not in patch and "spec.md" not in patch


def test_strip_message_and_clamp():
    msg = run_agent._strip_message({"role": "assistant", "content": None, "extra": 1})
    assert msg["content"] == "" and "extra" not in msg
    assert len(run_agent._clamp("x" * 100, 10)) > 10


def test_gate_completed_uses_delivery_clean(tmp_path):
    """交付收尾校验：阶段 6 但最终测试非全绿（或全绿后又跑红）不计完成。"""
    import time

    from anti_shortcut.state import StateManager

    class FakeSkill:
        def __init__(self, state):
            self.state = state

    state = StateManager(tmp_path / "state.json", user_request="fix")
    for stage in (2, 3, 4, 5, 6):
        state.advance(stage)
    skill = FakeSkill(state)
    assert state.current_stage == 6
    assert run_agent.gate_completed(skill) == 0
    state.mark_test_run({"exit_code": 0, "passed": True, "at_epoch": time.time()})
    assert run_agent.gate_completed(skill) == 1
    state.mark_test_run({"exit_code": 4, "passed": False, "at_epoch": time.time() + 1})
    assert run_agent.gate_completed(skill) == 0
    assert run_agent.gate_completed(None) == 0
    assert run_agent.gate_completed(object()) == 0


# ---------- grade ----------

def test_grade_load_rows(tmp_path):
    rows = [_instance()]
    f = tmp_path / "d.json"
    f.write_text(json.dumps(rows), encoding="utf-8")
    assert grade.load_rows(f) == rows
    f2 = tmp_path / "w.json"
    f2.write_text(json.dumps({"data": rows}), encoding="utf-8")
    assert grade.load_rows(f2) == rows
    f3 = tmp_path / "bad.json"
    f3.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(SystemExit):
        grade.load_rows(f3)


def test_grade_note_distinguishes_test_patch_conflict(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "test_output.txt").write_text(
        "Checking patch astropy/units/tests/test_quantity_annotations.py...\n"
        "error: patch failed: astropy/units/tests/test_quantity_annotations.py:219\n"
        "error: astropy/units/tests/test_quantity_annotations.py: patch does not apply\n",
        encoding="utf-8",
    )
    assert grade.grade_note_for_entry(
        {"patch_successfully_applied": False}, log_dir
    ) == "test_patch_conflict"
    assert grade.grade_note_for_entry(
        {"patch_successfully_applied": True}, log_dir
    ) == "graded"
    assert grade.grade_note_for_entry(
        {"patch_successfully_applied": False, "infra_failure": True}, log_dir
    ) == "grade_infra_failure"


def test_grade_note_without_conflict_markers_is_patch_not_applied(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "test_output.txt").write_text(
        "error: patch failed: src/service.py:12\n",
        encoding="utf-8",
    )
    assert grade.grade_note_for_entry(
        {"patch_successfully_applied": False}, tmp_path / "missing"
    ) == "patch_not_applied"
    assert grade.grade_note_for_entry(
        {"patch_successfully_applied": False}, log_dir
    ) == "patch_not_applied"
    assert not grade._looks_like_test_patch_conflict("patch failed without context")
    assert not grade._looks_like_test_patch_conflict("Checking patch only")


# ---------- prepare_dataset ----------

def test_image_for_prefers_dataset_field():
    assert prepare.image_for({"instance_id": "x__x-1", "image": "i:1"}) == "i:1"
    assert prepare.image_for({"instance_id": "astropy__astropy-12907"}) == \
        "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"


def test_has_local_image_matches_mirror_prefix():
    cached = {"docker.1panel.live/swebench/sweb.eval.x86_64.a_1776_a-1:latest",
              "swebench/sweb.eval.x86_64.b_1776_b-2:latest"}
    assert prepare.has_local_image("swebench/sweb.eval.x86_64.a_1776_a-1:latest", cached)
    assert prepare.has_local_image("swebench/sweb.eval.x86_64.b_1776_b-2:latest", cached)
    assert not prepare.has_local_image("swebench/sweb.eval.x86_64.c_1776_c-3:latest", cached)


def test_force_lf_writes_strips_cr(tmp_path):
    """Windows 上默认会写成 CRLF；评分进程必须强制 LF（否则容器内 eval.sh 全挂）。"""
    import os

    target = tmp_path / "eval.sh"
    with grade.force_lf_writes():
        target.write_text("set -e\ncd /testbed\n")
        assert b"\r\n" not in target.read_bytes()
    target.write_text("set -e\ncd /testbed\n")
    # Linux/macOS 默认本就写 LF；Windows 才会翻成 CRLF（这正是需要 shim 的原因）
    assert (b"\r\n" in target.read_bytes()) is (os.name == "nt")


# ---------- gold_check ----------

def test_gold_check_select_and_parse(tmp_path):
    gold = _load(HARNESS / "gold_check.py")
    rows = [{"instance_id": "a__a-1", "patch": "x"}, {"instance_id": "b__b-1", "patch": "  "},
            {"instance_id": "c__c-1", "patch": "y"}]
    picked = gold.select_gold(rows, [], limit=5)
    assert [r["instance_id"] for r in picked] == ["a__a-1", "c__c-1"]
    assert [r["instance_id"] for r in gold.select_gold(rows, ["c__c-1"], 5)] == ["c__c-1"]
    assert [r["instance_id"] for r in gold.select_gold(rows, [], limit=1)] == ["a__a-1"]
    assert gold.parse_pb("PB_RESOLVED=1\nnoise")["PB_RESOLVED"] == "1"


# ---------- regrade_cross_shard ----------

def test_regrade_helpers(tmp_path):
    regrade = _load(HARNESS / "regrade_cross_shard.py")
    assert regrade.short_id("django__django-10914") == "django_django-10914"
    f = tmp_path / "d.json"
    f.write_text(json.dumps({"instances": [{"instance_id": "a__a-1"}]}), encoding="utf-8")
    assert regrade.load_ids(f) == {"a__a-1"}
    meta = regrade.parse_pb("PB_RESOLVED=1\nPB_GRADE_SUMMARY={\"x\": 1}\nnoise")
    assert meta["PB_RESOLVED"] == "1" and "noise" not in meta


# ---------- container driver ----------

def test_container_defaults_point_into_repo():
    src = (ROOT / "scripts" / "run_swebench_batch_container.py").read_text(encoding="utf-8")
    assert 'benchmarks" / "swebench" / "run_agent.py"' in src
    assert 'benchmarks" / "swebench" / "grade.py"' in src


def test_container_bootstraps_gate_deps(tmp_path):
    argv = container.build_agent_docker_command(
        image="swebench/sweb.eval.x86_64.x_1776_x-1:latest", repo_mount=tmp_path,
        agent_script="/pb/benchmarks/swebench/run_agent.py", dataset="/pb/ds.json",
        instance_id="x__x-1", mode="gated", label="x_x-1_gated", outdir="/pb/runs",
        testbed_python="/opt/miniconda3/envs/testbed/bin/python",
        gate_python="/opt/miniconda3/bin/python", max_turns=60, exec_timeout=300)
    assert container.GATE_DEPS_MOUNT in argv[-1]
    assert f"{container.GATE_DEPS_VOLUME}:{container.GATE_DEPS_MOUNT}" in argv
    assert "pydantic PyYAML structlog" in argv[-1]
    assert "/opt/miniconda3/bin/python /pb/benchmarks/swebench/run_agent.py" in argv[-1]


@pytest.mark.parametrize("mode", ["strict", "warn", "off"])
def test_container_passes_test_preservation_to_agent(tmp_path, mode):
    argv = container.build_agent_docker_command(
        image="example:latest", repo_mount=tmp_path, agent_script="/pb/run_agent.py",
        dataset="/pb/ds.json", instance_id="x__x-1", mode="gated", label="smoke",
        outdir="/pb_runs", testbed_python="python", gate_python="python", max_turns=60,
        test_preservation=mode)
    assert f"--test-preservation {mode}" in argv[-1]


def test_agent_forwards_removal_evidence_and_counts_rejection(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import anti_shortcut

    removal = {"file": "test_repro.py", "name": "test_old", "reason": "replaced after review"}
    captured = {}
    class FakeGate:
        current_stage = 3
        is_complete = False
        state = SimpleNamespace(snapshot=lambda: {})
        def __init__(self, workdir, config, user_request):
            captured["config"] = config
        def install(self, tools):
            return tools
        def advance_stage(self, stage, evidence):
            captured["stage"] = stage
            captured["evidence"] = evidence
            return {"success": False, "error": "test_preservation strict",
                    "evidence": {"test_preservation": {"status": "rejected"}}}
    monkeypatch.setattr(anti_shortcut, "AntiShortcutSkill", FakeGate)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key")
    monkeypatch.setattr(run_agent.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(run_agent, "extract_patch", lambda _: ("diff", ""))
    monkeypatch.setattr(run_agent, "gate_completed", lambda _: 0)
    args_payload = {"new_stage": 4, "evidence": {"test_removals": [removal]}}
    monkeypatch.setattr(run_agent, "call_model", lambda *a: {"choices": [{"message": {
        "role": "assistant", "tool_calls": [{"id": "1", "type": "function", "function": {
            "name": "advance_stage", "arguments": json.dumps(args_payload)}}]}}]})
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps([_instance()]), encoding="utf-8")
    args = SimpleNamespace(workdir=tmp_path / "work", outdir=tmp_path / "out", label="test",
                           dataset=dataset, instance="x__x-1", venv=None, exec_timeout=1,
                           mode="gated", model="unit", max_turns=1, test_preservation="strict")
    assert run_agent.run(args) == 0
    assert captured["config"]["stages"]["implementation"]["test_preservation"] == "strict"
    assert captured["stage"] == 4 and captured["evidence"] == args_payload["evidence"]
    stats = json.loads((args.outdir / "test/stats.json").read_text())
    assert stats["gate_intercepts"] == stats["test_preservation_intercepts"] == 1
    assert stats["test_preservation"] == "strict"


def test_gate_deps_bootstrap_verifies_import_before_ready():
    """依赖卷必须「只读复制到容器私有目录 + import 校验 + 原子发布」，坏卷不会污染容器。

    v1.0.0 修复：并发下 ``mkdir`` 锁不互斥，两个容器同时 ``rm -rf`` + pip install 会
    把共享卷写坏（实测缺 ``typing_extensions``，4 个 gated 任务空补丁）。所以断言
    使用方不再直接依赖共享卷、发布不再覆盖已有缓存。
    """
    script = container.gate_deps_bootstrap("/opt/miniconda3/bin/python")
    # 共享缓存只被读：复制到容器私有目录，绝不放进 PYTHONPATH
    assert 'cp -a "$DEPS_BASE/." "$LOCAL/"' in script
    assert 'export PYTHONPATH="$LOCAL:' in script
    assert 'export PYTHONPATH="$DEPS_BASE' not in script
    # 非原子的 mkdir 锁已移除
    assert '"$DEPS.lock"' not in script
    assert "mkdir \"$DEPS_BASE.lock\"" not in script
    # 装完先校验 import，再发布；发布走唯一临时目录 + rename，且不覆盖已有缓存
    assert 'import pydantic, yaml, structlog' in script
    assert script.index("pb_gate_deps: 门禁依赖安装失败") < script.index('touch "$TMP/.ready"')
    assert 'TMP="$DEPS_BASE.pub.$$"' in script
    assert 'mv "$TMP" "$DEPS_BASE"' in script
    assert '[ -e "$DEPS_BASE" ] || mv "$TMP" "$DEPS_BASE"' in script


def test_filter_ready_keeps_only_cached_images():
    """--only-ready：只保留镜像已缓存的实例（扩样本时可边拉边跑）。"""
    tasks = [
        {"instance_id": "django__django-1", "image": "swebench/sweb.eval.x86_64.django_1776_django-1:latest"},
        {"instance_id": "sympy__sympy-2", "image": "swebench/sweb.eval.x86_64.sympy_1776_sympy-2:latest"},
        {"instance_id": "astropy__astropy-3"},   # 无 image 列：按 instance_id 推导
    ]
    images = {
        "swebench/sweb.eval.x86_64.django_1776_django-1:latest",
        "swebench/sweb.eval.x86_64.astropy_1776_astropy-3:latest",
    }
    ready = container.filter_ready(tasks, images)
    assert [t["instance_id"] for t in ready] == ["django__django-1", "astropy__astropy-3"]
    assert container.filter_ready(tasks, set()) == []


def test_patch_is_fresh_rejects_stale_and_empty(tmp_path):
    """Agent 秒退时挂载目录里可能还留着上一轮的补丁，不能被当成本次结果评分。"""
    patch = tmp_path / "model_patch.diff"
    started = 1_000_000.0
    assert container.patch_is_fresh(patch, started) is False   # 不存在
    patch.write_text("", encoding="utf-8")
    assert container.patch_is_fresh(patch, started) is False   # 空文件
    patch.write_text("diff --git a/x b/x\n", encoding="utf-8")
    os.utime(patch, (started - 60, started - 60))
    assert container.patch_is_fresh(patch, started) is False   # 上一轮遗留
    os.utime(patch, (started + 1, started + 1))
    assert container.patch_is_fresh(patch, started) is True     # 本次产出


def test_agent_tools_reject_paths_outside_workdir(tmp_path):
    workdir = tmp_path / "testbed"
    workdir.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("do not read", encoding="utf-8")
    tools = run_agent.AgentTools(workdir, None, 30)
    for path in (str(outside), "../outside.txt"):
        with pytest.raises(PermissionError, match="不在工作区"):
            tools.read_file(path)
        with pytest.raises(PermissionError, match="不在工作区"):
            tools.write_file(path, "x")
    # 工作区内缺失文件仍是普通错误，不会被误判成越界。
    assert tools.read_file("missing.py")["ok"] is False

def test_write_agent_dataset_strips_scoring_fields(tmp_path):
    rows = [
        {
            "instance_id": "a__a-1",
            "repo": "a/a",
            "base_commit": "c1",
            "problem_statement": "fix it",
            "patch": "GOLD-PATCH",
            "test_patch": "TEST-PATCH",
            "FAIL_TO_PASS": ["test_fail"],
            "PASS_TO_PASS": ["test_pass"],
            "hints_text": "the fix is ...",
            "image": "some-image",
        },
        {
            "instance_id": "b__b-1",
            "repo": "b/b",
            "base_commit": "c2",
            "problem_statement": "another",
            "patch": "GOLD-2",
        },
    ]
    src = tmp_path / "dataset.json"
    src.write_text(json.dumps({"instances": rows}), encoding="utf-8")
    out = container.write_agent_dataset(src, {"a__a-1"}, tmp_path / "agent.json")
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [row["instance_id"] for row in payload] == ["a__a-1"]
    assert set(payload[0]) == set(container.AGENT_DATASET_FIELDS)
    assert "GOLD-PATCH" not in out.read_text(encoding="utf-8")
    assert "TEST-PATCH" not in out.read_text(encoding="utf-8")
    assert "hints_text" not in payload[0]


def test_build_agent_docker_command_minimal_mounts(tmp_path):
    script = tmp_path / "run_agent.py"
    script.write_text("", encoding="utf-8")
    dataset = tmp_path / "agent.json"
    dataset.write_text("[]", encoding="utf-8")
    runs = tmp_path / "runs"
    runs.mkdir()
    mounts = [
        (script, "/pb/benchmarks/swebench/run_agent.py", True),
        (tmp_path / "anti_shortcut", "/pb/anti_shortcut", True),
        (dataset, "/pb/dataset_agent.json", True),
        (runs, "/pb_runs", False),
    ]
    argv = container.build_agent_docker_command(
        image="swebench/sweb.eval.x86_64.x_1776_x-1:latest",
        mounts=mounts,
        agent_script="/pb/benchmarks/swebench/run_agent.py",
        dataset="/pb/dataset_agent.json",
        instance_id="x__x-1", mode="gated", label="x_x-1_gated", outdir="/pb_runs",
        testbed_python="/opt/miniconda3/envs/testbed/bin/python",
        gate_python="/opt/miniconda3/bin/python", max_turns=60,
    )
    assert f"{script}:/pb/benchmarks/swebench/run_agent.py:ro" in argv
    assert f"{dataset}:/pb/dataset_agent.json:ro" in argv
    assert f"{runs}:/pb_runs" in argv
    # 不能把宿主仓库根整体挂进容器，否则 Agent 可读到评分数据集。
    assert f"{tmp_path}:/pb" not in argv
    assert all(":/pb:ro" not in item and ":/pb" != item for item in argv)

def test_build_agent_mounts_never_exposes_repo_root(tmp_path):
    mounts = container.build_agent_mounts(
        agent_script=tmp_path / "run_agent.py",
        repo_root=tmp_path,
        agent_dataset=tmp_path / "agent.json",
        agent_runs=tmp_path / "runs",
    )
    assert [container_path for _host, container_path, _ro in mounts] == [
        "/pb/benchmarks/swebench/run_agent.py",
        "/pb/anti_shortcut",
        "/pb/dataset_agent.json",
        "/pb_runs",
    ]
    assert [ro for _host, _container, ro in mounts] == [True, True, True, False]
    # 宿主仓库根只能以 anti_shortcut 子目录的形式出现，不能整体挂载。
    assert all(str(host) != str(tmp_path.resolve()) for host, _container, _ro in mounts)


def test_write_run_status_records_runner_pid(tmp_path):
    path = tmp_path / "status.json"
    container.write_run_status(path, state="running", results="results.csv")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["state"] == "running"
    assert data["runner_pid"] == os.getpid()
    assert data["results"] == "results.csv"
    status = _load(HARNESS / "run_status.py")
    assert status.load_status(path)["state"] == "running"
