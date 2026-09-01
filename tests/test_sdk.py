"""PhaseBarrier 编排器 SDK 测试（v0.22.0）。

覆盖：无参调用 / 状态查询、check 钩子（放行 / 拦截 / 跳步 / 非法参数 / 只读）、
advance 钩子、record_test_run、verify_evidence、CLI check 子命令、向后兼容。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill, PhaseBarrier
from anti_shortcut.config import STAGES

USER_REQUEST = "实现一个计算斐波那契数列第 n 项的函数 fib(n)"

SPEC = """# 斐波那契数列函数 Spec

## 需求分析
用户需要一个函数 fib(n)，返回斐波那契数列第 n 项。F(0)=0, F(1)=1。

## 设计方案
使用迭代法维护前两项，时间复杂度 O(n)。

## 接口定义
def fib(n: int) -> int
"""

TESTS = '''"""测试"""
import pytest
from fib import fib


def test_base_cases():
    assert fib(0) == 0
    assert fib(1) == 1


def test_known_value():
    assert fib(10) == 55


def test_rejects_negative():
    with pytest.raises(ValueError):
        fib(-1)
'''

IMPL = '''def fib(n: int) -> int:
    if not isinstance(n, int) or n < 0:
        raise ValueError("n must be a non-negative integer")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
'''


def _write(ws: Path, rel: str, content: str) -> None:
    target = ws / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _prepare(ws: Path) -> None:
    _write(ws, "pytest.ini", "[pytest]\ntestpaths = .\n")


def _write_spec(ws: Path) -> None:
    _write(ws, "spec.md", SPEC)


def _write_tests(ws: Path) -> None:
    _write(ws, "test_fib.py", TESTS)


def _write_impl(ws: Path) -> None:
    _write(ws, "fib.py", IMPL)


def _run_tests(ws: Path) -> dict:
    proc = subprocess.run(
        "python -m pytest test_fib.py -q -p no:cacheprovider",
        shell=True,
        cwd=ws,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {"exit_code": proc.returncode, "output": (proc.stdout or "") + (proc.stderr or "")}


class TestPhaseBarrierBasics:
    def test_no_arg_defaults_to_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        barrier = PhaseBarrier()
        try:
            assert barrier.workspace == tmp_path.resolve()
            assert barrier.inspect()["current_stage"] == 1
        finally:
            barrier.close()

    def test_inspect_structure(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path, user_request=USER_REQUEST)
        try:
            info = barrier.inspect()
            assert info["workspace"] == str(tmp_path.resolve())
            assert info["current_stage"] == 1
            assert info["stage_name"] == STAGES[1]
            assert info["completed_stages"] == [0]
            assert info["complete"] is False
            assert info["last_test_run"] is None
        finally:
            barrier.close()

    def test_wraps_skill_backward_compat(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            tools = barrier.install(
                {
                    "write_file": lambda p, c: None,
                    "execute_command": lambda c: {"exit_code": 0, "output": ""},
                }
            )
            assert "advance_stage" in tools
            # AntiShortcutSkill 原 API 不受影响
            skill = AntiShortcutSkill(tmp_path)
            try:
                assert skill.current_stage == barrier.skill.current_stage
                assert skill.advance_stage(2)["success"] is False  # 无 spec 证据
            finally:
                skill.close()
        finally:
            barrier.close()


class TestPhaseBarrierCheck:
    def test_stage1_allowed_at_bootstrap(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            result = barrier.check(1)
            assert result["allowed"] is True
            assert result["stage"] == 1
            assert result["current_stage"] == 1
            assert result["violations"] == []
            assert "放行" in result["message"] or "满足" in result["message"]
        finally:
            barrier.close()

    def test_stage0_allowed(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            assert barrier.check(0)["allowed"] is True
        finally:
            barrier.close()

    def test_denied_when_spec_missing(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            result = barrier.check(2)
            assert result["allowed"] is False
            assert result["violations"]
            assert "spec" in result["violations"][0].lower()
        finally:
            barrier.close()

    def test_allowed_after_spec(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            _write_spec(tmp_path)
            result = barrier.check(2)
            assert result["allowed"] is True
            assert result["stage_name"] == STAGES[2]
        finally:
            barrier.close()

    def test_denied_when_tests_missing_after_spec(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            _write_spec(tmp_path)
            assert barrier.check(3)["allowed"] is False
        finally:
            barrier.close()

    def test_allowed_after_tests(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            _write_spec(tmp_path)
            assert barrier.advance(2)["success"]
            _write_tests(tmp_path)
            result = barrier.check(3)
            assert result["allowed"] is True
        finally:
            barrier.close()

    def test_skip_detected(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            result = barrier.check(5)
            assert result["allowed"] is False
            assert "跳步" in result["message"]
            assert result["violations"]
        finally:
            barrier.close()

    @pytest.mark.parametrize("bad", [-1, 7, "2", 2.5, None, [], {}])
    def test_invalid_stage_returns_structured_denial(self, tmp_path, bad):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            result = barrier.check(bad)  # type: ignore[arg-type]
            assert result["allowed"] is False
            assert result["violations"]
        finally:
            barrier.close()

    def test_check_is_read_only(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            before = barrier.skill.state.snapshot()
            barrier.check(2)
            barrier.check(5)
            barrier.check(99)
            assert barrier.skill.state.snapshot() == before
        finally:
            barrier.close()


class TestPhaseBarrierAdvance:
    def test_advance_success_after_spec(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path, user_request=USER_REQUEST)
        try:
            _write_spec(tmp_path)
            result = barrier.advance(2)
            assert result["success"] is True
            assert result["stage"] == 2
            assert result["stage_name"] == STAGES[2]
            assert "message" in result
        finally:
            barrier.close()

    def test_advance_rejected_without_evidence(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            result = barrier.advance(2)
            assert result["success"] is False
            assert result["stage"] == 1
            assert "error" in result
        finally:
            barrier.close()

    def test_advance_rejects_skip(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            result = barrier.advance(3)
            assert result["success"] is False
            assert "跳" in result["error"] or "只能" in result["error"]
        finally:
            barrier.close()

    def test_full_sop_to_delivery(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path, user_request=USER_REQUEST)
        try:
            _write_spec(tmp_path)
            assert barrier.advance(2)["success"]
            _write_tests(tmp_path)
            assert barrier.advance(3)["success"]
            _write_impl(tmp_path)
            assert barrier.advance(4)["success"]

            record = barrier.record_test_run(_run_tests(tmp_path))
            assert record["passed"] is True

            result = barrier.advance(5)
            assert result["success"] is True
            assert result["stage"] == 6
            assert barrier.inspect()["complete"] is True
        finally:
            barrier.close()


class TestPhaseBarrierEvidence:
    def test_verify_evidence_ok_after_advance(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            _write_spec(tmp_path)
            barrier.advance(2)
            result = barrier.verify_evidence()
            assert result["ok"] is True
            assert result["violations"] == []
            assert result["signed"] is True
        finally:
            barrier.close()

    def test_verify_evidence_detects_tamper(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            _write_spec(tmp_path)
            barrier.advance(2)
            (tmp_path / "spec.md").write_text("# tampered\n" * 10, encoding="utf-8")
            result = barrier.verify_evidence()
            assert result["ok"] is False
            assert any("篡改" in v for v in result["violations"])
        finally:
            barrier.close()

    def test_verify_evidence_empty_manifest_fails(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            result = barrier.verify_evidence()
            assert result["ok"] is False
            assert result["violations"]
        finally:
            barrier.close()


class TestCheckCli:
    def test_cli_check_allowed(self, tmp_path):
        _prepare(tmp_path)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "anti_shortcut",
                "check",
                "--stage",
                "1",
                "--workspace",
                str(tmp_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["allowed"] is True
        assert payload["stage"] == 1

    def test_cli_check_denied(self, tmp_path):
        _prepare(tmp_path)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "anti_shortcut",
                "check",
                "--stage",
                "2",
                "--workspace",
                str(tmp_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 1
        payload = json.loads(proc.stdout)
        assert payload["allowed"] is False
        assert payload["violations"]

    def test_cli_check_invalid_stage(self, tmp_path):
        _prepare(tmp_path)
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "anti_shortcut",
                "check",
                "--stage",
                "9",
                "--workspace",
                str(tmp_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode == 1
        payload = json.loads(proc.stdout)
        assert payload["allowed"] is False


# ---------- v0.26.2：阶段清单与文件归属查询 ----------

class TestPhaseBarrierStageQueries:
    def test_list_stages_structure(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            stages = barrier.list_stages()
            assert [s["stage"] for s in stages] == [0, 1, 2, 3, 4, 5, 6]
            for s in stages:
                assert s["name"] and s["entry"] and s["evidence"]
            json.dumps(stages)  # 必须 JSON 可序列化（编排器透传）
        finally:
            barrier.close()

    def test_list_stages_names_match_stages(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            stages = barrier.list_stages()
            assert {s["stage"]: s["name"] for s in stages} == dict(STAGES)
        finally:
            barrier.close()

    def test_stage_of_spec_file(self, tmp_path):
        _prepare(tmp_path)
        _write_spec(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            info = barrier.stage_of("spec.md")
            assert info["stage"] == 1
            assert info["stage_name"] == "Spec 设计"
            assert info["kind"] == "spec"
            assert "阶段 1" in info["requires"]
        finally:
            barrier.close()

    def test_stage_of_test_file(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            info = barrier.stage_of("test_fib.py")
            assert info["stage"] == 2
            assert info["kind"] == "test"
            assert "重新运行测试" in info["requires"]
        finally:
            barrier.close()

    def test_stage_of_source_file(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            info = barrier.stage_of("fib.py")
            assert info["stage"] == 3
            assert info["kind"] == "source"
            assert "重新运行测试" in info["requires"]
        finally:
            barrier.close()

    def test_stage_of_other_file(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            info = barrier.stage_of("README.md")
            assert info["stage"] is None
            assert info["stage_name"] is None
            assert info["kind"] == "other"
            assert "无直接门禁影响" in info["requires"]
        finally:
            barrier.close()

    def test_stage_of_absolute_path(self, tmp_path):
        _prepare(tmp_path)
        barrier = PhaseBarrier(workspace=tmp_path)
        try:
            info = barrier.stage_of(tmp_path / "fib.py")
            assert info["stage"] == 3 and info["kind"] == "source"
            info2 = barrier.stage_of(tmp_path / "spec.md")
            assert info2["stage"] == 1 and info2["kind"] == "spec"
        finally:
            barrier.close()
