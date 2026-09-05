# -*- coding: utf-8 -*-
"""深度补全第一层测试（v0.50.0）：spec 具体性 + 测试断言质量。

覆盖：
- 278 字级“套话 spec”在五个维度全灭（实体 / 签名 / 决策 / 套话 / 需求锚点）；
- 一份有真实内容的 spec 五个维度全部通过；
- 需求锚点提取（latin 标识符 + 中文双字领域词，去停用词）；
- 具体实体 / 接口签名 / 决策表述 / 套话句式计数；
- SpecSpecificityValidator 校验器层与 Skill 端到端（阶段 1 拦截 / 放行）；
- analyze_test_assertion_quality 拒绝 assert True 等纯常数断言；
- TestAssertionQualityValidator 校验器层与 Skill 端到端（阶段 2 拦截 / 放行）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import anti_shortcut.config as pbconfig
import anti_shortcut.semantic as sem
from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig, SpecSpecificityOptions

# ---------- 夹具文本 ----------

FILLER_SPEC = """# 通用功能模块 Spec

## 需求分析
本方案将根据用户需求进行相应的设计，实现一个通用功能模块，满足用户全部需求，
并为后续业务扩展预留空间。我们会认真对待每一项用户诉求，提供优质服务。

## 设计方案
我们将采用合适的技术方案，综合考虑各种因素，确保系统的高效性、稳定性与可扩展性，
具体实现细节将在后续阶段补充细化。

## 接口定义
提供完整接口，支持各类调用场景，满足不同业务需求。

## 时间安排
我们将按照既定计划稳步推进，合理安排每个阶段的资源投入与交付节奏，确保按时高质量地完成全部目标。

## 质量保障
我们会持续跟踪交付质量，针对反馈快速响应并改进，让最终成果真正满足使用预期。
"""

REQ_LOGIN = (
    "用户需要一套登录鉴权能力，凭据正确放行；连续 5 次失败锁定账户 30 分钟；"
    "登录与锁定状态查询暴露给管理员"
)

RICH_SPEC = """# 登录鉴权模块 Spec

## 需求分析
用户需要一套登录鉴权能力：login(user, pwd) 凭据正确返回 True，错误次数超限锁定账户。
- REQ-001: login(user, pwd) 校验用户名与密码，凭据正确返回 True
- REQ-002: 连续 5 次失败锁定账户 30 分钟

## 设计方案
采用 HMAC-SHA256 存储密码摘要，而非明文或 MD5；引入失败计数器并持久化到
state_store，避免内存态在进程重启后丢失；登录接口使用固定时间比较防止时序侧信道。

## 接口定义
- def login(user: str, pwd: str) -> bool
- def lock_status(user: str) -> LockState
- 输入：user 与 pwd 为字符串；输出：bool 或 LockState
- POST /api/v1/login 与 POST /api/v1/lock-status
"""

REQ_FIB = "实现一个计算斐波那契数列第 n 项的函数 fib(n)，负数输入抛 ValueError，超大 n 要求高性能"

RICH_FIB_SPEC = """# fib(n) 计算模块 Spec

## 需求分析
用户需要一个计算斐波那契数列第 n 项的函数 fib(n)，负数输入抛 ValueError。
- REQ-001: fib(n) 返回第 n 个斐波那契数，n 从 0 开始
- REQ-002: 负数输入抛 ValueError；非整数输入抛 TypeError

## 设计方案
采用迭代滚动更新，而非递归；对超大 n 使用矩阵快速幂加速，避免指数级栈溢出；
结果缓存到 memo 字典，便于重复调用。

## 接口定义
- def fib(n: int) -> int
- def fib_fast(n: int, memo: dict) -> int
- def fib_iter(n: int) -> int
- 输入：整数 n >= 0；输出：第 n 个斐波那契数
"""

WEAK_TESTS = """def test_one():
    assert True


def test_two():
    assert 1 == 1
"""

STRONG_TESTS = """def test_zero():
    assert fib(0) == 0


def test_ten():
    assert fib(10) == 55
"""


class _FakeState:
    def __init__(self, user_request: str = "") -> None:
        self._user_request = user_request

    def get_evidence(self, key, default=None):
        if key == "user_request":
            return self._user_request or default
        return default


def _spec_config(**overrides) -> GateConfig:
    cfg = GateConfig(spec_file="spec.md")
    for key, value in overrides.items():
        setattr(cfg.semantic.spec_specificity, key, value)
    return cfg


# ---------- 278 字套话回归（方案验收标准） ----------

class TestFillerSpecRegression:
    def test_filler_length_is_278_scale(self):
        assert 250 <= len(FILLER_SPEC) <= 320, len(FILLER_SPEC)

    def test_filler_fails_all_dimensions(self):
        analysis = sem.analyze_spec_specificity(FILLER_SPEC, REQ_LOGIN)
        assert analysis["ok"] is False
        checks = analysis["checks"]
        assert checks["concrete_entities"]["value"] < 5
        assert checks["interface_signatures"]["value"] < 2
        assert checks["decision_phrases"]["value"] < 1
        assert checks["filler_phrases"]["value"] > 1
        assert checks["requirement_anchors"]["value"] < 2

    def test_rich_spec_passes_all_dimensions(self):
        analysis = sem.analyze_spec_specificity(RICH_SPEC, REQ_LOGIN)
        assert analysis["ok"] is True
        assert analysis["checks"]["concrete_entities"]["value"] >= 5
        assert analysis["checks"]["interface_signatures"]["value"] >= 2
        assert analysis["checks"]["decision_phrases"]["value"] >= 1
        assert analysis["checks"]["filler_phrases"]["value"] <= 1


# ---------- 提取 / 计数辅助 ----------

class TestSpecHelpers:
    def test_request_anchors_latin_and_cjk(self):
        anchors = sem.extract_request_anchors(REQ_FIB)
        assert "fib" in anchors
        assert "斐波" in anchors and "数列" in anchors
        # 泛指词“函数 / 一个”不因单独出现而成为锚点
        assert sem.extract_request_anchors("实现一个函数") == []

    def test_request_anchors_empty(self):
        assert sem.extract_request_anchors("") == []

    def test_entities_exclude_keywords_and_numbers(self):
        spec = "REQ-001: 任意说明 def login(user: str) -> bool；class Token:\n    value = 1"
        entities = sem.extract_concrete_entities(spec)
        assert "login" in entities and "user" in entities
        assert "Token" in entities and "value" in entities
        assert "def" not in entities and "class" not in entities
        assert not any(e.isdigit() for e in entities)

    def test_entities_api_and_backticks(self):
        spec = "端点 `GET /api/v1/orders` 与 POST /api/v1/users，另见 `rate_limit`"
        entities = sem.extract_concrete_entities(spec)
        assert any("GET /api/v1/orders" in e for e in entities)
        assert any("POST /api/v1/users" in e for e in entities)
        assert any("rate_limit" in e for e in entities)

    def test_interface_signatures_counts_markers(self):
        sigs = sem.extract_interface_signatures(RICH_SPEC)
        assert len(sigs) >= 4
        assert any("def login" in s for s in sigs)
        assert any("POST /api/v1" in s for s in sigs)
        assert sem.extract_interface_signatures(FILLER_SPEC) == []

    def test_decision_phrases(self):
        assert sem.count_decision_phrases(RICH_SPEC) >= 1
        assert sem.count_decision_phrases(FILLER_SPEC) == 0

    def test_filler_hits_and_custom_patterns(self):
        assert sem.count_filler_hits(FILLER_SPEC) >= 1
        assert sem.count_filler_hits(RICH_SPEC) == 0
        # 自定义模式（含非法正则）不崩溃
        assert sem.count_filler_hits(FILLER_SPEC, [r"满足用户"]) == 1
        assert sem.count_filler_hits(FILLER_SPEC, [r"([unclosed"]) == 0


# ---------- SpecSpecificityValidator ----------

class TestSpecSpecificityValidator:
    def _check(self, workspace: Path, request: str, config: GateConfig):
        v = sem.SpecSpecificityValidator()
        return v.check(workspace, config, _FakeState(request))

    def test_skip_when_spec_missing(self, tmp_path: Path):
        res = self._check(tmp_path, REQ_LOGIN, _spec_config())
        assert res.ok and res.evidence["skipped"] == "no_spec"

    def test_reject_filler_with_actionable_message(self, tmp_path: Path):
        (tmp_path / "spec.md").write_text(FILLER_SPEC, encoding="utf-8")
        cfg = _spec_config()
        cfg.semantic.spec_specificity.enabled = True
        res = self._check(tmp_path, REQ_LOGIN, cfg)
        assert not res.ok
        assert "疑似套话" in res.message
        assert "具体实体" in res.message and "接口签名" in res.message
        assert "套话句式" in res.message

    def test_pass_rich_spec(self, tmp_path: Path):
        (tmp_path / "spec.md").write_text(RICH_SPEC, encoding="utf-8")
        cfg = _spec_config()
        cfg.semantic.spec_specificity.enabled = True
        res = self._check(tmp_path, REQ_LOGIN, cfg)
        assert res.ok
        assert "spec 具体性通过" in res.message

    def test_contract_and_options(self):
        v = sem.SpecSpecificityValidator()
        assert v.name == "spec_specificity"
        assert v.stages == (1,)
        with pytest.raises(ValidationError):
            SpecSpecificityOptions(min_entities=-1)
        with pytest.raises(ValidationError):
            SpecSpecificityOptions(max_filler_hits=-1)
        with pytest.raises(ValidationError):
            SpecSpecificityOptions(filler_patterns=[])
        with pytest.raises(ValidationError):
            SpecSpecificityOptions(stages=[7])

    def test_default_disabled(self):
        assert GateConfig().semantic.spec_specificity.enabled is False


# ---------- 断言质量 ----------

class TestAssertionQuality:
    def test_weak_file_detected(self):
        info = sem.analyze_test_assertion_quality(WEAK_TESTS)
        assert info["ok"] is False
        names = [f["name"] for f in info["weak_functions"]]
        assert "test_one" in names and "test_two" in names

    def test_strong_file_ok(self):
        info = sem.analyze_test_assertion_quality(STRONG_TESTS)
        assert info["ok"] is True
        assert info["test_functions"] == 2

    def test_syntax_error_ignored(self):
        info = sem.analyze_test_assertion_quality("def test_x(:\n")
        assert info["ok"] is True and info["parse_error"] is True

    def test_mixed_file_only_flags_weak_functions(self):
        src = WEAK_TESTS + STRONG_TESTS
        info = sem.analyze_test_assertion_quality(src)
        assert info["ok"] is False
        assert [f["name"] for f in info["weak_functions"]] == ["test_one", "test_two"]

    def test_assert_with_reference_is_ok(self):
        assert sem.analyze_test_assertion_quality("def test_a():\n    assert fib(1) is True\n")["ok"] is True
        assert sem.analyze_test_assertion_quality("def test_b():\n    assert user.age == 18\n")["ok"] is True


class TestAssertionQualityValidator:
    def _check(self, workspace: Path, config: GateConfig):
        v = sem.TestAssertionQualityValidator()
        return v.check(workspace, config, _FakeState())

    def _enabled(self, strict: bool = True) -> GateConfig:
        cfg = GateConfig(spec_file="spec.md")
        opts = cfg.semantic.test_assertion_quality
        opts.enabled = True
        opts.strict = strict
        return cfg

    def test_skip_non_python(self, tmp_path: Path):
        (tmp_path / "login.dart").write_text("void main() {}", encoding="utf-8")
        cfg = GateConfig(spec_file="spec.md", language="dart")
        res = self._check(tmp_path, cfg)
        assert res.ok and res.evidence["skipped"] == "not_python"

    def test_skip_when_not_strict(self, tmp_path: Path):
        (tmp_path / "test_a.py").write_text(WEAK_TESTS, encoding="utf-8")
        res = self._check(tmp_path, self._enabled(strict=False))
        assert res.ok and res.evidence["skipped"] == "not_strict"

    def test_reject_weak_tests(self, tmp_path: Path):
        (tmp_path / "test_a.py").write_text(WEAK_TESTS, encoding="utf-8")
        res = self._check(tmp_path, self._enabled())
        assert not res.ok
        assert "纯常数断言" in res.message
        assert "test_a.py:test_one" in res.message
        assert res.evidence["weak_functions"][0]["file"] == "test_a.py"

    def test_pass_strong_tests(self, tmp_path: Path):
        (tmp_path / "test_a.py").write_text(STRONG_TESTS, encoding="utf-8")
        res = self._check(tmp_path, self._enabled())
        assert res.ok

    def test_default_disabled(self):
        assert GateConfig().semantic.test_assertion_quality.enabled is False
        with pytest.raises(ValidationError):
            pbconfig.TestAssertionQualityOptions(stages=[])


# ---------- Skill 端到端 ----------

def _make_skill(ws: Path, config: GateConfig, request: str) -> AntiShortcutSkill:
    (ws / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    return AntiShortcutSkill(ws, user_request=request, config=config)


class TestSkillSpecSpecificity:
    def test_filler_blocked_then_rich_passes(self, tmp_path: Path):
        ws = tmp_path
        cfg = _spec_config()
        cfg.semantic.spec_specificity.enabled = True
        skill = _make_skill(ws, cfg, REQ_FIB)

        (ws / "spec.md").write_text(FILLER_SPEC, encoding="utf-8")
        r = skill.advance_stage(2)
        assert r["success"] is False
        assert "spec 具体性未通过" in r["error"]
        assert "疑似套话" in r["error"]
        assert skill.current_stage == 1

        (ws / "spec.md").write_text(RICH_FIB_SPEC, encoding="utf-8")
        r = skill.advance_stage(2)
        assert r["success"] is True and r["stage"] == 2
        assert r["evidence"]["semantic"]["semantic_checks"][0]["ok"] is True


class TestSkillAssertionQuality:
    def test_weak_tests_blocked_then_strong_passes(self, tmp_path: Path):
        ws = tmp_path
        cfg = GateConfig(spec_file="spec.md")
        cfg.semantic.test_assertion_quality.enabled = True
        skill = _make_skill(ws, cfg, REQ_FIB)

        (ws / "spec.md").write_text(RICH_FIB_SPEC, encoding="utf-8")
        assert skill.advance_stage(2)["success"] is True

        (ws / "test_fib.py").write_text(WEAK_TESTS, encoding="utf-8")
        r = skill.advance_stage(3)
        assert r["success"] is False
        assert "断言质量未通过" in r["error"]
        assert "纯常数断言" in r["error"]

        (ws / "test_fib.py").write_text(STRONG_TESTS, encoding="utf-8")
        r = skill.advance_stage(3)
        assert r["success"] is True and r["stage"] == 3


class TestDefaultOff:
    def test_run_semantic_checks_stage1_noop(self, tmp_path: Path):
        cfg = GateConfig(spec_file="spec.md")
        ok, msg, ev = sem.run_semantic_checks(tmp_path, cfg, _FakeState(), 1)
        assert ok and msg == "" and ev == {}