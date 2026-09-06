# -*- coding: utf-8 -*-
"""深度补全第二层测试（v0.51.0）：实现-文档双向追踪 + 断言目标数严格档。

覆盖：
- analyze_test_assertion_quality 的 min_assert_targets 严格档（constant_only /
  too_few_targets 两种弱函数归因）；
- extract_traceable_spec_entities / extract_public_symbols 提取辅助；
- analyze_implementation_traceability 正向命中（整词标识符 / API 路径）与反向
  undeclared 报告；
- ImplementationTraceabilityValidator 校验器层（跳过 / 通过 / 拒绝 / 阈值 /
  report_undeclared）；
- Skill 端到端：阶段 3 拦截“spec 承诺了 fib_fast 但实现没写” -> 补全后放行。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import anti_shortcut.config as pbconfig
import anti_shortcut.semantic as sem
from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig, ImplementationTraceabilityOptions
from anti_shortcut.languages import PythonAdapter

# ---------- 夹具 ----------

TRACE_SPEC = """# 斐波那契计算模块 Spec

## 需求分析
用户需要一个计算斐波那契数列第 n 项的函数，负数输入抛 ValueError。
- REQ-001: fib(n) 返回第 n 个斐波那契数，n 从 0 开始
- REQ-002: 负数输入抛 ValueError

## 设计方案
采用迭代滚动更新，而非递归，避免指数级栈溢出；大 n 用 fib_fast 加速。

## 接口定义
- def fib(n: int) -> int
- def fib_fast(n: int, memo: dict) -> int
"""

VAGUE_SPEC = """# 通用模块 Spec

## 需求分析
本方案将根据用户需求进行相应设计，满足用户全部需求并提供优质服务。

## 设计方案
我们将采用合适的技术方案，综合考虑各种因素，确保系统的高效性与稳定性。

## 接口定义
提供完整接口，支持各类调用场景。
"""

TRACE_IMPL = """def fib(n):
    if n < 0:
        raise ValueError("n must be >= 0")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def fib_fast(n, memo=None):
    if n < 0:
        raise ValueError("n must be >= 0")
    memo = memo or {}
    if n in memo:
        return memo[n]
    if n < 2:
        return n
    memo[n] = fib_fast(n - 1, memo) + fib_fast(n - 2, memo)
    return memo[n]
"""

PARTIAL_IMPL = """def fib(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
"""

TRACE_TESTS = """from fib import fib, fib_fast


def test_zero():
    assert fib(0) == 0


def test_ten():
    assert fib(10) == 55
    assert fib_fast(20) == 6765
"""

MULTI_TARGET_TESTS = """def test_two_targets():
    assert fib(0) == 0
    assert fib_fast(1) == 1
"""

SINGLE_TARGET_TESTS = """def test_one_target():
    assert fib(0) == 0
"""


class _FakeAdapter:
    def __init__(self, name: str = "python") -> None:
        self.name = name

    def is_source_file(self, path, config=None):
        return Path(path).suffix == ".py" and not Path(path).name.startswith("test_")

    def is_test_file(self, path, config=None):
        return Path(path).name.startswith("test_") and Path(path).suffix == ".py"


class _FakeState:
    def get_evidence(self, key, default=None):
        return default


def _trace_config(**overrides) -> GateConfig:
    cfg = GateConfig(spec_file="spec.md")
    for key, value in overrides.items():
        setattr(cfg.semantic.implementation_traceability, key, value)
    return cfg


def _enable(cfg: GateConfig) -> GateConfig:
    cfg.semantic.implementation_traceability.enabled = True
    return cfg


def _write(workspace: Path, name: str, text: str) -> None:
    (workspace / name).write_text(text, encoding="utf-8")


# ---------- 断言目标数严格档 ----------

class TestAssertionTargets:
    def test_default_zero_keeps_single_target_ok(self):
        info = sem.analyze_test_assertion_quality(SINGLE_TARGET_TESTS)
        assert info["ok"] is True

    def test_too_few_targets_flagged_with_reason(self):
        info = sem.analyze_test_assertion_quality(SINGLE_TARGET_TESTS, min_assert_targets=2)
        assert info["ok"] is False
        weak = info["weak_functions"][0]
        assert weak["name"] == "test_one_target"
        assert weak["reason"] == "too_few_targets"
        assert weak["targets"] == ["fib"]

    def test_multi_targets_pass(self):
        info = sem.analyze_test_assertion_quality(MULTI_TARGET_TESTS, min_assert_targets=2)
        assert info["ok"] is True
        assert info["weak_functions"] == []

    def test_attribute_chain_counts_one_root(self):
        src = "def test_u():\n    assert user.age == 18\n    assert user.name == 'a'\n"
        info1 = sem.analyze_test_assertion_quality(src, min_assert_targets=1)
        assert info1["ok"] is True
        info2 = sem.analyze_test_assertion_quality(src, min_assert_targets=2)
        assert info2["ok"] is False
        assert info2["weak_functions"][0]["targets"] == ["user"]

    def test_constant_assert_still_constant_only(self):
        info = sem.analyze_test_assertion_quality(
            "def test_a():\n    assert True\n", min_assert_targets=2
        )
        assert info["ok"] is False
        assert info["weak_functions"][0]["reason"] == "constant_only"

    def test_mixed_constant_and_real_counts_real_targets(self):
        src = "def test_a():\n    assert True\n    assert fib(0) == 0\n"
        info = sem.analyze_test_assertion_quality(src, min_assert_targets=1)
        assert info["ok"] is True
        info2 = sem.analyze_test_assertion_quality(src, min_assert_targets=2)
        assert info2["ok"] is False
        assert info2["weak_functions"][0]["reason"] == "too_few_targets"

    def test_options_validation(self):
        opts = pbconfig.TestAssertionQualityOptions(min_assert_targets=1, stages=[2])
        assert opts.min_assert_targets == 1
        with pytest.raises(ValidationError):
            pbconfig.TestAssertionQualityOptions(min_assert_targets=-1)
        with pytest.raises(ValidationError):
            pbconfig.TestAssertionQualityOptions(stages=[7])
        with pytest.raises(ValidationError):
            pbconfig.TestAssertionQualityOptions(stages=[])

    def test_validator_rejects_low_targets_and_passes_multi(self, tmp_path: Path):
        v = sem.TestAssertionQualityValidator()
        cfg = GateConfig(spec_file="spec.md")
        opts = cfg.semantic.test_assertion_quality
        opts.enabled = True
        opts.min_assert_targets = 2
        _write(tmp_path, "test_a.py", SINGLE_TARGET_TESTS)
        res = v.check(tmp_path, cfg, _FakeState(), _FakeAdapter())
        assert not res.ok
        assert "断言目标 1 < 2" in res.message
        assert "test_a.py:test_one_target" in res.message

        (tmp_path / "test_a.py").write_text(MULTI_TARGET_TESTS, encoding="utf-8")
        res = v.check(tmp_path, cfg, _FakeState(), _FakeAdapter())
        assert res.ok


# ---------- 提取辅助 ----------

class TestTraceHelpers:
    def test_traceable_entities_filters(self):
        spec = "REQ-001: 描述 def login(user, pwd) 与 POST /api/v1/login；变量 n 忽略"
        entities = sem.extract_traceable_spec_entities(spec)
        assert "login" in entities and "POST /api/v1/login" in entities
        assert "n" not in entities and "REQ" not in entities

    def test_public_symbols(self):
        src = "def login():\n    pass\n\nclass _Secret:\n    pass\n\nasync def _priv():\n    pass\n\nclass User:\n    pass\n"
        assert sem.extract_public_symbols(src) == ["login", "User"]
        assert sem.extract_public_symbols("def x(:\n") == []

    def test_analyze_resolved_and_missing(self):
        analysis = sem.analyze_implementation_traceability(TRACE_SPEC, [TRACE_IMPL])
        assert analysis["ok"] is True
        assert analysis["resolved"] == ["fib", "fib_fast", "memo"]
        assert analysis["missing"] == []

        partial = sem.analyze_implementation_traceability(TRACE_SPEC, [PARTIAL_IMPL])
        assert partial["ok"] is False
        assert partial["missing"] == ["fib_fast", "memo"]

    def test_word_boundary_avoids_false_positive(self):
        # fib 不应因 fibonacci 出现而误判命中
        analysis = sem.analyze_implementation_traceability(
            "def fib(n: int) -> int", ["def fibonacci(n):\n    return n\n"]
        )
        assert analysis["missing"] == ["fib"]

    def test_api_path_matches_substring(self):
        spec = "POST /api/v1/orders"
        impl = 'routes = {"orders": "/api/v1/orders"}\n'
        analysis = sem.analyze_implementation_traceability(spec, [impl])
        assert analysis["ok"] is True
        analysis2 = sem.analyze_implementation_traceability(spec, ["x = 1\n"])
        assert analysis2["missing"] == ["POST /api/v1/orders"]

    def test_undeclared_reported(self):
        analysis = sem.analyze_implementation_traceability("def fib(n): ...", ["def logout():\n    pass\n"])
        assert analysis["undeclared"] == ["logout"]


# ---------- ImplementationTraceabilityValidator ----------

class TestTraceValidator:
    def _check(self, workspace: Path, config: GateConfig):
        v = sem.ImplementationTraceabilityValidator()
        return v.check(workspace, config, _FakeState(), PythonAdapter())

    def test_skip_non_python(self, tmp_path: Path):
        res = sem.ImplementationTraceabilityValidator().check(
            tmp_path, _trace_config(), _FakeState(), _FakeAdapter(name="java")
        )
        assert res.ok and res.evidence["skipped"] == "not_python"

    def test_skip_when_spec_missing(self, tmp_path: Path):
        _write(tmp_path, "fib.py", TRACE_IMPL)
        res = self._check(tmp_path, _trace_config())
        assert res.ok and res.evidence["skipped"] == "no_spec"

    def test_skip_vague_spec_too_few_entities(self, tmp_path: Path):
        _write(tmp_path, "spec.md", VAGUE_SPEC)
        _write(tmp_path, "fib.py", TRACE_IMPL)
        res = self._check(tmp_path, _trace_config())
        assert res.ok and res.evidence["skipped"] == "too_few_entities"

    def test_reject_missing_entity(self, tmp_path: Path):
        _write(tmp_path, "spec.md", TRACE_SPEC)
        _write(tmp_path, "fib.py", PARTIAL_IMPL)
        res = self._check(tmp_path, _enable(_trace_config()))
        assert not res.ok
        assert "实现未覆盖 spec 承诺实体（缺失 2/3）：fib_fast、memo" in res.message
        assert "已覆盖：fib" in res.message
        assert res.evidence["missing"] == ["fib_fast", "memo"]
        assert res.evidence["files"] == ["fib.py"]

    def test_pass_when_covered(self, tmp_path: Path):
        _write(tmp_path, "spec.md", TRACE_SPEC)
        _write(tmp_path, "fib.py", TRACE_IMPL)
        res = self._check(tmp_path, _enable(_trace_config()))
        assert res.ok
        assert "实现覆盖 spec 承诺实体 3/3" in res.message

    def test_max_missing_allows_small_gap(self, tmp_path: Path):
        _write(tmp_path, "spec.md", TRACE_SPEC)
        _write(tmp_path, "fib.py", PARTIAL_IMPL)
        res = self._check(tmp_path, _enable(_trace_config(max_missing=2)))
        assert res.ok

    def test_report_undeclared_off_strips_evidence(self, tmp_path: Path):
        spec = "def fib(n: int) -> int\n"
        _write(tmp_path, "spec.md", spec)
        _write(tmp_path, "fib.py", "def fib(n):\n    return n\n\ndef logout():\n    pass\n")
        cfg = _trace_config(report_undeclared=False)
        res = self._check(tmp_path, _enable(cfg))
        assert res.ok and res.evidence["undeclared"] == []
        cfg2 = _trace_config(report_undeclared=True)
        res2 = self._check(tmp_path, _enable(cfg2))
        assert res2.ok and res2.evidence["undeclared"] == ["logout"]
        assert "logout" in res2.message

    def test_options_and_defaults(self):
        cfg = GateConfig()
        assert cfg.semantic.implementation_traceability.enabled is False
        valid = ImplementationTraceabilityOptions(min_entities=2, max_missing=1, stages=[3])
        assert valid.max_missing == 1
        with pytest.raises(ValidationError):
            ImplementationTraceabilityOptions(min_entities=-1)
        with pytest.raises(ValidationError):
            ImplementationTraceabilityOptions(max_missing=-1)
        with pytest.raises(ValidationError):
            ImplementationTraceabilityOptions(stages=[])
        with pytest.raises(ValidationError):
            ImplementationTraceabilityOptions(stages=[9])
        v = sem.ImplementationTraceabilityValidator()
        assert v.name == "implementation_traceability"
        assert v.stages == (3,)


# ---------- Skill 端到端（阶段 3） ----------

def _make_skill(ws: Path, config: GateConfig, request: str) -> AntiShortcutSkill:
    (ws / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    return AntiShortcutSkill(ws, user_request=request, config=config)


class TestSkillTraceability:
    def test_missing_entity_blocked_then_fixed_passes(self, tmp_path: Path):
        ws = tmp_path
        cfg = _enable(_trace_config())
        skill = _make_skill(ws, cfg, "实现一个 fib(n) 与 fib_fast(n) 函数")

        _write(ws, "spec.md", TRACE_SPEC)
        assert skill.advance_stage(2)["success"] is True

        _write(ws, "test_fib.py", TRACE_TESTS)
        assert skill.advance_stage(3)["success"] is True

        _write(ws, "fib.py", PARTIAL_IMPL)
        r = skill.advance_stage(4)
        assert r["success"] is False
        assert "实现未覆盖 spec 承诺实体" in r["error"]
        assert "fib_fast" in r["error"]
        assert skill.current_stage == 3

        _write(ws, "fib.py", TRACE_IMPL)
        r = skill.advance_stage(4)
        assert r["success"] is True and r["stage"] == 4
        assert r["evidence"]["semantic"]["semantic_checks"][0]["ok"] is True


class TestDefaultOffTrace:
    def test_run_semantic_checks_stage3_noop(self, tmp_path: Path):
        cfg = GateConfig(spec_file="spec.md")
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 3, PythonAdapter())
        assert ok and msg == "" and ev == {}