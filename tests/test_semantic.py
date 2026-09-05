# -*- coding: utf-8 -*-
"""语义级校验测试（v0.49.0）：需求追踪 + 变异测试 + 插件契约 + Skill 接线。

覆盖：
- REQ 提取 / 归一 / 去重、测试引用大小写 / 空白容错；
- RequirementCoverageValidator 的跳过 / 通过 / 拒绝 / 部分覆盖阈值；
- AST 变异体生成的算子矩阵（BinOp / Compare / BoolOp / Not / 字符串拼接豁免）
  与确定性采样；
- run_mutation_suite 三态统计（killed / survived / error，含超时）；
- MutationScoreValidator 的跳过路径与真实 pytest 工作区强 / 弱测试对比；
- 注册 / 入口点加载去重 / run_semantic_checks 开关与失败聚合；
- plugin-verify 对 semantic_validators 组的契约校验；
- Skill 端到端：阶段 2 需求追踪拒绝与放行、阶段 4 变异门禁拒绝与放行。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

import anti_shortcut.plugins as plugins_mod
import anti_shortcut.semantic as sem
from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig
from conftest import USER_REQUEST

# ---------- 常量 ----------

SPEC_WITH_REQS = """# 登录模块 Spec

## 需求分析
用户需要一套简单的用户名密码登录能力，覆盖正常登录与密码错误两种场景。
- REQ-001: 支持用户名密码登录，凭据正确返回 True
- REQ-002: 密码错误时返回 False 且不抛异常

## 设计方案
采用纯函数实现 login(user, pwd)，内部只做字符串比对，不引入外部依赖与状态。

## 接口定义
def login(user: str, pwd: str) -> bool
"""

LOGIN_IMPL = """def login(user, pwd):
    return user == "a" and pwd == "b"
"""

STRONG_LOGIN_TESTS = """from login import login

# REQ-001
def test_login_ok():
    assert login("a", "b") is True

# REQ-002
def test_login_bad_pwd():
    assert login("a", "x") is False
    assert login("b", "b") is False
"""

WEAK_LOGIN_TESTS = """from login import login


def test_login_ok():
    assert login("a", "b") is True


def test_login_returns_bool_for_any_input():
    assert isinstance(login("x", "y"), bool) is True
"""

CALC_IMPL = """def classify(x, limit):
    if x > limit and x != 0:
        return x + 1
    return x - 1
"""

STRONG_CALC_TESTS = """from calc import classify


def test_branch_if():
    assert classify(5, 3) == 6


def test_branch_else():
    assert classify(0, 3) == -1
    assert classify(-5, 3) == -6


def test_boundary_gt():
    assert classify(3, 3) == 2


def test_not_eq_zero():
    assert classify(0, -1) == 1
"""

WEAK_CALC_TESTS = """from calc import classify


def test_one():
    assert classify(5, 3) == 6
"""


# ---------- 辅助 ----------

class _FakeAdapter:
    def __init__(self, name: str = "python") -> None:
        self.name = name

    def is_test_file(self, path, config=None):
        return Path(path).name.startswith("test_") and Path(path).suffix == ".py"

    def is_source_file(self, path, config=None):
        return Path(path).suffix == ".py" and not self.is_test_file(path)


class _FakeState:
    def __init__(self, evidence: dict | None = None) -> None:
        self._evidence = dict(evidence or {})

    def get_evidence(self, key, default=None):
        return self._evidence.get(key, default)

    def set_evidence(self, key, value):
        self._evidence[key] = value


def _write(ws: Path, name: str, content: str) -> Path:
    p = ws / name
    p.write_text(content, encoding="utf-8")
    return p


def _mutation_config(**overrides) -> GateConfig:
    cfg = GateConfig(spec_file="spec.md")
    opts = cfg.semantic.mutation_score
    for key, value in overrides.items():
        setattr(opts, key, value)
    return cfg


# ---------- REQ 提取 ----------

class TestRequirementParsing:
    def test_extract_ids_order_dedup_normalize(self):
        text = "REQ-1 与 REQ-002 重复 REQ-2；REQ-1 再次出现；REQ-123456 大编号"
        assert sem.extract_requirement_ids(text) == ["REQ-001", "REQ-002", "REQ-123456"]

    def test_extract_ids_ignore_invalid(self):
        assert sem.extract_requirement_ids("PREQ-001 REQ-X REQ-01a 无需求") == []

    def test_extract_refs_case_insensitive_spacing(self):
        text = "#REQ-001\n#REQ-001 重复\n#\tREQ-002  \n# req-003 小写\n普通行 REQ-004 无井号不匹配"
        assert sem.extract_test_references(text) == ["REQ-001", "REQ-002", "REQ-003"]

    def test_result_evidence_default_empty(self):
        res = sem.SemanticCheckResult(True, "ok", None)
        assert res.evidence == {}


# ---------- RequirementCoverageValidator ----------

class TestRequirementCoverage:
    def _check(self, workspace: Path, **overrides) -> sem.SemanticCheckResult:
        cfg = GateConfig(spec_file="spec.md")
        for key, value in overrides.items():
            setattr(cfg.semantic.requirement_coverage, key, value)
        validator = sem.RequirementCoverageValidator()
        return validator.check(workspace, cfg, _FakeState(), _FakeAdapter())

    def test_skip_when_spec_missing(self, tmp_path: Path):
        res = self._check(tmp_path)
        assert res.ok and "未声明 REQ" in res.message
        assert res.evidence["note"] == "no_reqs"

    def test_skip_when_spec_has_no_reqs(self, tmp_path: Path):
        _write(tmp_path, "spec.md", "# Spec\n## 需求分析\n没有编号需求\n## 设计方案\nx\n## 接口定义\ndef f()\n")
        res = self._check(tmp_path)
        assert res.ok and res.evidence["note"] == "no_reqs"

    def test_pass_when_all_covered(self, tmp_path: Path):
        _write(tmp_path, "spec.md", SPEC_WITH_REQS)
        _write(tmp_path, "test_login.py", STRONG_LOGIN_TESTS)
        res = self._check(tmp_path)
        assert res.ok
        assert res.evidence["coverage"] == 100.0
        assert res.evidence["covered"] == ["REQ-001", "REQ-002"]

    def test_fail_when_uncovered(self, tmp_path: Path):
        _write(tmp_path, "spec.md", SPEC_WITH_REQS)
        _write(tmp_path, "test_login.py", "# REQ-001\ndef test_login_ok():\n    assert True\n")
        res = self._check(tmp_path)
        assert not res.ok
        assert "REQ-002" in res.message
        assert "覆盖率 50.0% < 100.0%" in res.message
        assert res.evidence["uncovered"] == ["REQ-002"]

    def test_fail_lists_all_uncovered(self, tmp_path: Path):
        _write(tmp_path, "spec.md", SPEC_WITH_REQS)
        _write(tmp_path, "test_login.py", "def test_nothing():\n    pass\n")
        res = self._check(tmp_path)
        assert not res.ok
        assert "REQ-001" in res.message and "REQ-002" in res.message
        assert res.evidence["coverage"] == 0.0

    def test_partial_coverage_meets_min(self, tmp_path: Path):
        _write(tmp_path, "spec.md", SPEC_WITH_REQS)
        _write(tmp_path, "test_login.py", "# REQ-001\ndef test_login_ok():\n    assert True\n")
        res = self._check(tmp_path, min_coverage=50.0)
        assert res.ok
        assert res.evidence["coverage"] == 50.0

    def test_contract(self):
        v = sem.RequirementCoverageValidator()
        assert v.name == "requirement_coverage"
        assert v.stages == (2,)
        assert v.config_key() == "requirement_coverage"
        assert callable(v.check)

    def test_options_validation(self):
        from anti_shortcut.config import MutationScoreOptions, RequirementCoverageOptions

        with pytest.raises(ValidationError):
            RequirementCoverageOptions(min_coverage=120)
        with pytest.raises(ValidationError):
            RequirementCoverageOptions(stages=[])
        with pytest.raises(ValidationError):
            MutationScoreOptions(min_score=-1)
        with pytest.raises(ValidationError):
            MutationScoreOptions(max_mutants=0)
        with pytest.raises(ValidationError):
            MutationScoreOptions(timeout_per_mutant=0)
        with pytest.raises(ValidationError):
            MutationScoreOptions(stages=[7])

    def test_defaults_disabled(self):
        cfg = GateConfig()
        assert cfg.semantic.requirement_coverage.enabled is False
        assert cfg.semantic.mutation_score.enabled is False


class TestBaseContractAndDefensivePaths:
    def test_base_check_raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            sem.SemanticValidator().check(None, None, None)

    def test_apply_site_unparse_failure_returns_original(self, monkeypatch):
        def boom(tree):
            raise RuntimeError("unparse boom")

        monkeypatch.setattr(sem.ast, "unparse", boom)
        ok, mutated = sem._apply_site("def f(a, b):\n    return a + b\n", "x.py", 0)
        assert ok is False and "return a + b" in mutated

    def test_make_mutant_workdir_retries_on_collision(self, monkeypatch):
        import os

        real = os.makedirs
        calls = {"n": 0}

        def fake(path, mode=0o777):
            calls["n"] += 1
            if calls["n"] == 1:
                raise FileExistsError()
            return real(path, mode=mode)

        monkeypatch.setattr(sem.os, "makedirs", fake)
        d = sem._make_mutant_workdir()
        try:
            assert d.is_dir() and calls["n"] >= 2
        finally:
            d.rmdir()

    def test_make_mutant_workdir_exhausted_raises(self, monkeypatch):
        def always_exists(*args, **kwargs):
            raise FileExistsError()

        monkeypatch.setattr(sem.os, "makedirs", always_exists)
        with pytest.raises(RuntimeError):
            sem._make_mutant_workdir()


# ---------- 变异体生成 ----------

class TestMutationGeneration:
    def test_all_operator_sites(self):
        src = (
            "def f(a, b, c, s):\n"
            "    x = a + b\n"
            "    y = a - b\n"
            "    z = a * b\n"
            "    w = a // b\n"
            "    t = s + 'suffix'\n"
            "    if a > b and a != b:\n"
            "        return a < b or a >= b\n"
            "    if not (a <= b):\n"
            "        return a == b\n"
            "    return a + b\n"
        )
        muts = sem.generate_mutations(src, filename="m.py", max_mutants=0)
        ops = [m["op"] for m in muts]
        # Add 两处（赋值 + return）；字符串拼接 `s + 'suffix'` 豁免不生成
        assert ops.count("binop:Add") == 2
        assert "binop:Sub" in ops
        assert "binop:Mult" in ops
        assert "binop:FloorDiv" in ops
        assert "cmp:Gt" in ops and "cmp:NotEq" in ops
        assert "cmp:Lt" in ops and "cmp:GtE" in ops and "cmp:LtE" in ops and "cmp:Eq" in ops
        assert "bool:And" in ops and "bool:Or" in ops
        assert "unary:not" in ops
        # 每个变异体源码与原文不同且可编译
        for m in muts:
            assert m["source"] != src
            compile(m["source"], m["file"], "exec")
        assert muts[0]["file"] == "m.py"

    def test_max_mutants_zero_returns_all(self):
        src = "def f(a, b):\n    return a + b\n"
        assert len(sem.generate_mutations(src, max_mutants=0)) == 1

    def test_max_mutants_caps_and_deterministic(self):
        src = "\n".join("x%d = a + b" % i for i in range(10))
        a = sem.generate_mutations(src, max_mutants=3, seed=42)
        b = sem.generate_mutations(src, max_mutants=3, seed=42)
        assert len(a) == 3
        assert [m["lineno"] for m in a] == [m["lineno"] for m in b]

    def test_syntax_error_returns_empty(self):
        assert sem.generate_mutations("def f(:\n") == []

    def test_no_sites_returns_empty(self):
        assert sem.generate_mutations("x = 'pure string constant'\n") == []


# ---------- run_mutation_suite 三态 ----------

class TestMutationSuiteStats:
    def _suite(self, monkeypatch, workspace: Path, rc: int):
        monkeypatch.setattr(
            sem, "_execute_mutant", lambda copy_dir, command, timeout: (rc, 0.05)
        )
        mutants = [{"file": "calc.py", "lineno": 2, "op": "cmp:Gt", "source": "x"}]
        return sem.run_mutation_suite(workspace, mutants, ["python", "-m", "pytest"], 10)

    def test_killed(self, tmp_path: Path, monkeypatch):
        report = self._suite(monkeypatch, tmp_path, 1)
        assert report["stats"]["killed"] == 1 and report["stats"]["score"] == 100.0
        assert report["mutants"][0]["result"] == "killed"

    def test_survived(self, tmp_path: Path, monkeypatch):
        report = self._suite(monkeypatch, tmp_path, 0)
        assert report["stats"]["survived"] == 1 and report["stats"]["score"] == 0.0

    def test_error_and_timeout(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            sem, "_execute_mutant", lambda copy_dir, command, timeout: (2, 0.05)
        )
        report = sem.run_mutation_suite(tmp_path, [{"file": "a.py", "lineno": 1, "op": "x", "source": "y"}], ["x"], 1)
        assert report["stats"]["error"] == 1 and report["stats"]["score"] is None

        def _boom(*args, **kwargs):
            raise subprocess.TimeoutExpired(kwargs.get("command") or args[0], timeout=1)

        monkeypatch.setattr(sem.subprocess, "run", _boom)
        report = sem.run_mutation_suite(tmp_path, [{"file": "a.py", "lineno": 1, "op": "x", "source": "y"}], ["x"], 1)
        assert report["stats"]["error"] == 1

    def test_partial_score(self, tmp_path: Path, monkeypatch):
        calls = {"n": 0}
        original = sem._execute_mutant

        def fake(copy_dir, command, timeout):
            calls["n"] += 1
            return (1 if calls["n"] % 2 else 0, 0.05)

        monkeypatch.setattr(sem, "_execute_mutant", fake)
        mutants = [
            {"file": "a.py", "lineno": 1, "op": "x", "source": "y1"},
            {"file": "a.py", "lineno": 2, "op": "y", "source": "y2"},
            {"file": "a.py", "lineno": 3, "op": "z", "source": "y3"},
            {"file": "a.py", "lineno": 4, "op": "w", "source": "y4"},
        ]
        report = sem.run_mutation_suite(tmp_path, mutants, ["x"], 1)
        assert report["stats"]["killed"] == 2 and report["stats"]["survived"] == 2
        assert report["stats"]["score"] == 50.0
        assert original is not None


# ---------- MutationScoreValidator（跳过路径 + mock 统计） ----------

class TestMutationScoreSkips:
    def _check(self, workspace: Path, state: _FakeState, adapter, config: GateConfig | None = None):
        v = sem.MutationScoreValidator()
        return v.check(workspace, config or _mutation_config(), state, adapter)

    def test_skip_non_python(self, tmp_path: Path):
        res = self._check(tmp_path, _FakeState(), _FakeAdapter(name="java"))
        assert res.ok and res.evidence["skipped"] == "not_python"

    def test_skip_when_last_test_not_passed(self, tmp_path: Path):
        _write(tmp_path, "calc.py", CALC_IMPL)
        _write(tmp_path, "test_calc.py", STRONG_CALC_TESTS)
        state = _FakeState({"last_test_run": {"passed": False, "exit_code": 1}})
        res = self._check(tmp_path, state, _FakeAdapter())
        assert res.ok and res.evidence["skipped"] == "tests_not_passed"

    def test_skip_no_sources_or_tests(self, tmp_path: Path):
        state = _FakeState({"last_test_run": {"passed": True, "exit_code": 0}})
        res = self._check(tmp_path, state, _FakeAdapter())
        assert res.ok and res.evidence["skipped"] == "no_sources_or_tests"
        _write(tmp_path, "calc.py", CALC_IMPL)
        res = self._check(tmp_path, state, _FakeAdapter())
        assert res.ok and res.evidence["skipped"] == "no_sources_or_tests"

    def test_skip_no_mutants(self, tmp_path: Path):
        _write(tmp_path, "calc.py", "NAME = 'const'\n")
        _write(tmp_path, "test_calc.py", "def test_name():\n    assert NAME == 'const'\n")
        state = _FakeState({"last_test_run": {"passed": True, "exit_code": 0}})
        res = self._check(tmp_path, state, _FakeAdapter())
        assert res.ok and res.evidence["skipped"] == "no_mutants"

    def test_fail_all_error_state(self, tmp_path: Path, monkeypatch):
        _write(tmp_path, "calc.py", CALC_IMPL)
        _write(tmp_path, "test_calc.py", STRONG_CALC_TESTS)
        monkeypatch.setattr(sem, "_execute_mutant", lambda cd, cmd, to: (2, 0.05))
        state = _FakeState({"last_test_run": {"passed": True, "exit_code": 0}})
        res = self._check(tmp_path, state, _FakeAdapter())
        assert not res.ok
        assert "无法运行" in res.message and "error" in res.evidence["stats"]
        assert res.evidence["command"] == [
            sem.sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        ]

    def test_fail_below_min_score(self, tmp_path: Path, monkeypatch):
        _write(tmp_path, "calc.py", CALC_IMPL)
        _write(tmp_path, "test_calc.py", STRONG_CALC_TESTS)
        monkeypatch.setattr(
            sem, "_execute_mutant", lambda cd, cmd, to: (0, 0.05)
        )
        state = _FakeState({"last_test_run": {"passed": True, "exit_code": 0}})
        cfg = _mutation_config(min_score=80.0, max_mutants=20)
        res = self._check(tmp_path, state, _FakeAdapter(), cfg)
        assert not res.ok
        assert "0.0% < 80.0%" in res.message and "补充真实断言" in res.message

    def test_custom_command_and_sampling(self, tmp_path: Path, monkeypatch):
        _write(tmp_path, "calc.py", CALC_IMPL)
        _write(tmp_path, "test_calc.py", STRONG_CALC_TESTS)
        calls = {"n": 0}

        def fake(cd, cmd, to):
            calls["n"] += 1
            return (1, 0.05)

        monkeypatch.setattr(sem, "_execute_mutant", fake)
        state = _FakeState({"last_test_run": {"passed": True, "exit_code": 0}})
        cfg = _mutation_config(min_score=50.0, max_mutants=2, command=["py", "-m", "pytest"])
        res = self._check(tmp_path, state, _FakeAdapter(), cfg)
        assert res.ok
        assert calls["n"] == 2
        assert res.evidence["command"] == ["py", "-m", "pytest"]


# ---------- MutationScoreValidator（真实 pytest 工作区） ----------

class TestMutationScoreReal:
    def _check(self, workspace: Path):
        cfg = _mutation_config(min_score=80.0, timeout_per_mutant=30.0)
        state = _FakeState({"last_test_run": {"passed": True, "exit_code": 0}})
        v = sem.MutationScoreValidator()
        return v.check(workspace, cfg, state, _FakeAdapter())

    def test_strong_tests_pass(self, tmp_path: Path):
        _write(tmp_path, "pytest.ini", "[pytest]\ntestpaths = .\n")
        _write(tmp_path, "calc.py", CALC_IMPL)
        _write(tmp_path, "test_calc.py", STRONG_CALC_TESTS)
        res = self._check(tmp_path)
        assert res.ok
        assert res.evidence["stats"]["score"] == 100.0
        assert res.evidence["stats"]["survived"] == 0

    def test_weak_tests_fail(self, tmp_path: Path):
        _write(tmp_path, "pytest.ini", "[pytest]\ntestpaths = .\n")
        _write(tmp_path, "calc.py", CALC_IMPL)
        _write(tmp_path, "test_calc.py", WEAK_CALC_TESTS)
        res = self._check(tmp_path)
        assert not res.ok
        assert res.evidence["stats"]["score"] < 80.0
        assert res.evidence["stats"]["survived"] > 0


# ---------- 注册 / 入口点 / run_semantic_checks ----------

class _PassValidator(sem.SemanticValidator):
    name = "pass_check"
    description = "always ok"
    stages = (2,)

    def check(self, workspace, config, state, adapter=None):
        return sem.SemanticCheckResult(True, "pass_check ok", {"n": 1})


class _FailValidator(sem.SemanticValidator):
    name = "fail_check"
    description = "always fail"
    stages = (2,)

    def check(self, workspace, config, state, adapter=None):
        return sem.SemanticCheckResult(False, "fail_check rejected", {})


class _BoomValidator(sem.SemanticValidator):
    name = "boom_check"
    stages = (2,)

    def check(self, workspace, config, state, adapter=None):
        raise RuntimeError("kaboom")


@pytest.fixture(autouse=True)
def _registered_semantic_validators():
    """把测试用自定义语义校验器挂到进程内注册表（用例结束清空）。"""
    sem._custom_semantic_validators.clear()
    sem._custom_semantic_validators.extend([_PassValidator(), _FailValidator(), _BoomValidator()])
    yield
    sem._custom_semantic_validators.clear()


class TestPluginMechanics:
    def test_register_validation(self):
        sem._custom_semantic_validators.clear()
        try:
            with pytest.raises(TypeError):
                sem.register_semantic_validator(object())
            nameless = _PassValidator()
            nameless.name = ""
            with pytest.raises(ValueError):
                sem.register_semantic_validator(nameless)
            v = _PassValidator()
            sem.register_semantic_validator(v)
            assert sem._custom_semantic_validators[-1] is v
        finally:
            sem._custom_semantic_validators.clear()

    def test_load_plugins_dedup_and_entry_points(self, monkeypatch):
        class FakeEP:
            def __init__(self, name, obj):
                self.name = name
                self._obj = obj

            def load(self):
                if isinstance(self._obj, Exception):
                    raise self._obj
                return self._obj

        sem._custom_semantic_validators.clear()
        try:
            monkeypatch.setattr(
                sem.metadata,
                "entry_points",
                lambda group=None: [
                    FakeEP("fail_check", _FailValidator),
                    FakeEP("broken", RuntimeError("bad plugin")),
                    FakeEP("factory", lambda: _PassValidator()),
                ],
            )
            loaded = sem.load_semantic_plugins()
            names = [v.name for v in loaded]
            assert "requirement_coverage" in names and "mutation_score" in names
            assert "fail_check" in names and "pass_check" in names
            assert "broken" not in names
            # 进程内注册同名覆盖内置
            sem.register_semantic_validator(_PassValidator())
            loaded2 = sem.load_semantic_plugins()
            names2 = [v.name for v in loaded2]
            assert "requirement_coverage" in names2
        finally:
            sem._custom_semantic_validators.clear()

    def test_load_plugins_old_entrypoint_api(self, monkeypatch):
        class LegacyValidator(sem.SemanticValidator):
            name = "legacy_check"
            stages = (2,)

            def check(self, workspace, config, state, adapter=None):
                return sem.SemanticCheckResult(True, "legacy ok", {})

        class FakeEP:
            name = "legacy_check"

            def load(self):
                return LegacyValidator()

        calls = {"n": 0}

        def fake_entry_points(group=None):
            calls["n"] += 1
            if group is not None:
                raise TypeError("new-style group kw unsupported")
            return {"phase_barrier.semantic_validators": [FakeEP()]}

        monkeypatch.setattr(sem.metadata, "entry_points", fake_entry_points)
        loaded = sem.load_semantic_plugins()
        assert any(v.name == "legacy_check" for v in loaded)
        assert calls["n"] >= 2

    def test_coerce_invalid_objects(self):
        assert sem._coerce_semantic_validator(object()) is None
        assert sem._coerce_semantic_validator(lambda: object()) is None

        class BoomClass:
            def __init__(self):
                raise RuntimeError()

        assert sem._coerce_semantic_validator(BoomClass) is None
        assert sem._coerce_semantic_validator(lambda: (_ for _ in ()).throw(RuntimeError())) is None

    def test_run_checks_disabled_returns_empty_ok(self, tmp_path: Path):
        cfg = GateConfig()
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 2)
        assert ok and msg == "" and ev == {}

    def test_run_checks_stage_filtering(self, tmp_path: Path):
        cfg = GateConfig()
        cfg.semantic.requirement_coverage.enabled = True
        cfg.semantic.requirement_coverage.min_coverage = 0.0
        # 阶段 2 有校验器；阶段 3 无
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 3)
        assert ok and ev == {}
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 2)
        assert ok and "requirement_coverage" in msg

    def test_run_checks_typed_field_enabled(self, tmp_path: Path):
        cfg = GateConfig()
        cfg.semantic.requirement_coverage.enabled = True
        cfg.semantic.requirement_coverage.min_coverage = 0.0
        _write(tmp_path, "spec.md", "# Spec\n## 需求分析\nREQ-001: 任意\n## 设计方案\nx\n## 接口定义\ndef f()\n")
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 2)
        assert ok and "requirement_coverage" in msg
        assert ev["semantic_checks"][0]["ok"] is True

    def test_run_checks_plugin_options_dict_section(self, tmp_path: Path):
        cfg = GateConfig()
        cfg.semantic.plugin_options["fail_check"] = {"enabled": True}
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 2)
        assert not ok
        assert "[fail_check]" in msg and "fail_check rejected" in msg

    def test_run_checks_exception_isolated(self, tmp_path: Path):
        cfg = GateConfig()
        cfg.semantic.plugin_options["boom_check"] = {"enabled": True}
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 2)
        assert not ok
        assert "boom_check" in msg and "RuntimeError" in msg

    def test_run_checks_aggregates_pass_and_fail(self, tmp_path: Path):
        cfg = GateConfig()
        cfg.semantic.plugin_options["pass_check"] = {"enabled": True}
        cfg.semantic.plugin_options["fail_check"] = {"enabled": True}
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 2)
        assert not ok
        assert ev["semantic_checks"][0]["ok"] is True
        assert ev["semantic_checks"][1]["ok"] is False


# ---------- plugin-verify 契约 ----------

class _FakeEP:
    def __init__(self, name, obj):
        self.name = name
        self._obj = obj

    def load(self):
        return self._obj


class TestPluginVerifySemanticGroup:
    def _verify(self, obj):
        return plugins_mod._verify_entry("phase_barrier.semantic_validators", _FakeEP("x", obj))

    def test_instance_ok(self):
        ok, errors = self._verify(_PassValidator())
        assert ok and errors == []

    def test_class_ok(self):
        ok, errors = self._verify(_PassValidator)
        assert ok and errors == []

    def test_class_instantiation_error(self):
        class Boom:
            def __init__(self):
                raise RuntimeError("ctor")

        ok, errors = self._verify(Boom)
        assert not ok and any("实例化失败" in e for e in errors)

    def test_missing_name(self):
        class NoName(_PassValidator):
            name = ""

        ok, errors = self._verify(NoName())
        assert not ok and any("非空 name" in e for e in errors)

    def test_missing_stages(self):
        class NoStages(sem.SemanticValidator):
            name = "x"

            def check(self, workspace, config, state, adapter=None):
                return sem.SemanticCheckResult(True, "", {})

        ok, errors = self._verify(NoStages())
        assert not ok and any("stages" in e for e in errors)

    def test_invalid_stages(self):
        class BadStages(_PassValidator):
            stages = (True, 9)

        ok, errors = self._verify(BadStages())
        assert not ok and any("stages" in e for e in errors)

    def test_missing_check(self):
        class NoCheck(sem.SemanticValidator):
            name = "x"
            stages = (2,)
            check = None  # type: ignore[assignment]

        ok, errors = self._verify(NoCheck())
        assert not ok and any("check(" in e for e in errors)


# ---------- Skill 端到端 ----------

def _make_skill(ws: Path, config: GateConfig) -> AntiShortcutSkill:
    _write(ws, "pytest.ini", "[pytest]\ntestpaths = .\n")
    return AntiShortcutSkill(ws, user_request=USER_REQUEST, config=config)


def _req_config() -> GateConfig:
    cfg = GateConfig(spec_file="spec.md")
    cfg.semantic.requirement_coverage.enabled = True
    cfg.semantic.requirement_coverage.min_coverage = 100.0
    return cfg


class TestSkillRequirementCoverage:
    def test_blocked_without_refs_then_passes(self, tmp_path: Path):
        ws = tmp_path
        skill = _make_skill(ws, _req_config())
        _write(ws, "spec.md", SPEC_WITH_REQS)
        assert skill.advance_stage(2)["success"] is True

        _write(ws, "test_login.py", WEAK_LOGIN_TESTS)
        r = skill.advance_stage(3)
        assert r["success"] is False
        assert "语义校验未通过" in r["error"]
        assert "REQ-001" in r["error"] and "REQ-002" in r["error"]
        assert r["evidence"]["semantic_checks"][0]["name"] == "requirement_coverage"

        _write(ws, "test_login.py", STRONG_LOGIN_TESTS)
        r = skill.advance_stage(3)
        assert r["success"] is True and r["stage"] == 3
        assert "semantic" in r["evidence"]
        assert r["evidence"]["semantic"]["semantic_checks"][0]["ok"] is True


class TestSkillMutationGateReal:
    def _config(self) -> GateConfig:
        return _mutation_config(
            enabled=True, min_score=80.0, timeout_per_mutant=30.0
        )

    def test_weak_blocked_then_strong_passes(self, tmp_path: Path, monkeypatch):
        import anti_shortcut.skill as skill_module

        ws = tmp_path
        cfg = self._config()
        skill = _make_skill(ws, cfg)
        _write(ws, "spec.md", SPEC_WITH_REQS)
        assert skill.advance_stage(2)["success"] is True
        _write(ws, "test_login.py", WEAK_LOGIN_TESTS)
        assert skill.advance_stage(3)["success"] is True
        _write(ws, "login.py", LOGIN_IMPL)
        assert skill.advance_stage(4)["success"] is True

        # 弱测试通过 -> 阶段 4 变异门禁拒绝（阶段 4 特判：调用 advance_stage(5)）
        record = {"passed": True, "exit_code": 0}
        skill.state.mark_test_run(record)
        r = skill.advance_stage(5)
        assert r["success"] is False
        assert "变异测试未通过" in r["error"]
        assert r["evidence"]["semantic_checks"][0]["name"] == "mutation_score"

        # 补强测试并重测 -> 变异门禁通过，测试全绿跳过修复直达交付
        _write(ws, "test_login.py", STRONG_LOGIN_TESTS)
        skill.state.mark_test_run(record)
        r = skill.advance_stage(5)
        assert r["success"] is True and r["stage"] == 6
        assert "变异测试通过" in r["evidence"]["semantic"]["semantic_checks"][0]["message"]


class TestSkillSemanticWiring:
    def test_failure_blocks_advance(self, tmp_path: Path, monkeypatch):
        import anti_shortcut.skill as skill_module

        ws = tmp_path
        skill = _make_skill(ws, GateConfig())
        _write(ws, "spec.md", SPEC_WITH_REQS)
        calls = {"n": 0}

        def fake_run(workspace, config, state, stage, adapter=None):
            calls["n"] += 1
            return False, "语义校验未通过：模拟", {"semantic_checks": []}

        monkeypatch.setattr(skill_module, "run_semantic_checks", fake_run)
        r = skill.advance_stage(2)
        assert r["success"] is False and r["error"] == "语义校验未通过：模拟"
        assert calls["n"] == 1
        assert skill.current_stage == 1

    def test_success_merges_evidence(self, tmp_path: Path, monkeypatch):
        import anti_shortcut.skill as skill_module

        ws = tmp_path
        skill = _make_skill(ws, GateConfig())
        _write(ws, "spec.md", SPEC_WITH_REQS)
        calls = {"n": 0}

        def fake_run(workspace, config, state, stage, adapter=None):
            calls["n"] += 1
            return True, "语义校验通过", {"semantic_checks": [{"name": "demo", "ok": True}]}

        monkeypatch.setattr(skill_module, "run_semantic_checks", fake_run)
        r = skill.advance_stage(2)
        assert r["success"] is True and r["stage"] == 2
        assert calls["n"] == 1
        assert r["evidence"]["semantic"]["semantic_checks"][0]["name"] == "demo"