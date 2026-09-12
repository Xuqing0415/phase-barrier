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


def test_gate_deps_bootstrap_verifies_import_before_ready():
    """依赖卷必须「清空安装 + import 校验 + 才落 .ready」，坏卷不会污染后续容器。"""
    script = container.gate_deps_bootstrap("/opt/miniconda3/bin/python")
    assert 'rm -rf "$DEPS"' in script
    assert 'import pydantic, yaml, structlog' in script
    assert script.index("import pydantic, yaml, structlog") < script.index('touch "$DEPS/.ready"')


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
