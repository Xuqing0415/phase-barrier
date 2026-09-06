# -*- coding: utf-8 -*-
"""可选“命名语义”严格档测试（断言质量之上，累积 main 未发版）。

覆盖：
- require_meaningful_names 默认关闭：test_1 / test_a 仅凭真实断言即可通过；
- 开启后：无行为动词且未映射 spec 功能关键词的占位名 -> generic_name；
- 行为动词名（test_should_return_* / test_raises_on_*）通过；
- 映射 spec 功能关键词名（test_login_ok 映射 login）通过；
- 泛化词（str / req 等签名类型词）不计入 spec 关键词映射；
- Validator 层与 Skill 端到端：占位名被拦截 -> 更名后放行。
"""
from __future__ import annotations

from pathlib import Path

import anti_shortcut.semantic as sem
from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig

GENERIC_TESTS = """def test_one():
    assert fib(0) == 0


def test_a():
    assert login("u", "p") is True
"""

VERB_TESTS = """def test_should_return_fib_zero():
    assert fib(0) == 0


def test_raises_on_negative():
    try:
        fib(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
"""

KEYWORD_TESTS = """def test_login_ok():
    assert login("u", "p") is True


def test_fib_zero():
    assert fib(0) == 0
"""

REQ_SPEC = """# 登录鉴权与 fib Spec

## 需求分析
用户需要 login(user, pwd) 与 fib(n)。
- REQ-001: login(user, pwd) 凭据正确返回 True
- REQ-002: fib(n) 返回第 n 个斐波那契数

## 设计方案
选择 HMAC 摘要而非明文；fib 用迭代滚动而非递归。

## 接口定义
- def login(user: str, pwd: str) -> bool
- def fib(n: int) -> int
- 输入：user / pwd / n
- POST /api/v1/login
"""


class _FakeState:
    def get_evidence(self, key, default=None):
        return default


class TestAnalyzeNaming:
    def test_default_off_keeps_generic_ok(self):
        info = sem.analyze_test_assertion_quality(GENERIC_TESTS)
        assert info["ok"] is True

    def test_generic_flagged_when_required(self):
        info = sem.analyze_test_assertion_quality(
            GENERIC_TESTS, require_meaningful_names=True
        )
        assert info["ok"] is False
        reasons = {f["name"]: f["reason"] for f in info["weak_functions"]}
        assert reasons == {"test_one": "generic_name", "test_a": "generic_name"}

    def test_verb_names_pass(self):
        info = sem.analyze_test_assertion_quality(VERB_TESTS, require_meaningful_names=True)
        assert info["ok"] is True

    def test_spec_keyword_mapping_pass(self):
        spec_entities = sem.extract_concrete_entities(REQ_SPEC)
        assert "login" in spec_entities and "fib" in spec_entities
        info = sem.analyze_test_assertion_quality(
            KEYWORD_TESTS,
            require_meaningful_names=True,
            spec_entities=spec_entities,
        )
        assert info["ok"] is True

    def test_generic_tokens_do_not_count_as_mapping(self):
        # spec 只有 def f(x: str) -> str 这类签名泛化词时，test_a 仍是占位名
        spec_entities = sem.extract_concrete_entities("def f(x: str) -> str")
        info = sem.analyze_test_assertion_quality(
            GENERIC_TESTS,
            require_meaningful_names=True,
            spec_entities=spec_entities,
        )
        assert {f["name"] for f in info["weak_functions"]} == {"test_one", "test_a"}

    def test_constant_only_takes_priority(self):
        src = "def test_x():\n    assert True\n"
        info = sem.analyze_test_assertion_quality(src, require_meaningful_names=True)
        assert info["weak_functions"][0]["reason"] == "constant_only"

    def test_no_assert_functions_not_naming_flagged(self):
        src = "def test_a():\n    pass\n\ndef test_should_pass():\n    assert fib(1) == 1\n"
        info = sem.analyze_test_assertion_quality(src, require_meaningful_names=True)
        # test_a 无断言由结构校验（require_assert_per_test）拦截，命名档只查含断言函数
        assert info["weak_functions"] == []

    def test_api_path_entity_tokens_map(self):
        spec_entities = ["POST /api/v1/login"]
        src = "def test_login_status():\n    assert login_status() == 200\n"
        info = sem.analyze_test_assertion_quality(
            src, require_meaningful_names=True, spec_entities=spec_entities
        )
        assert info["ok"] is True


class TestNamingValidator:
    def _cfg(self, **overrides):
        cfg = GateConfig(spec_file="spec.md")
        opts = cfg.semantic.test_assertion_quality
        opts.enabled = True
        for key, value in overrides.items():
            setattr(opts, key, value)
        return cfg

    def _check(self, ws: Path, cfg: GateConfig):
        return sem.TestAssertionQualityValidator().check(ws, cfg, _FakeState())

    def test_default_off_passes_generic(self, tmp_path):
        (tmp_path / "spec.md").write_text(REQ_SPEC, encoding="utf-8")
        (tmp_path / "test_x.py").write_text(GENERIC_TESTS, encoding="utf-8")
        res = self._check(tmp_path, self._cfg())
        assert res.ok

    def test_reject_generic_names(self, tmp_path):
        (tmp_path / "spec.md").write_text(REQ_SPEC, encoding="utf-8")
        (tmp_path / "test_x.py").write_text(GENERIC_TESTS, encoding="utf-8")
        res = self._check(tmp_path, self._cfg(require_meaningful_names=True))
        assert not res.ok
        assert "函数名无行为动词" in res.message
        assert "test_x.py:test_one" in res.message
        assert {f["reason"] for f in res.evidence["weak_functions"]} == {"generic_name"}

    def test_pass_verb_and_keyword_names(self, tmp_path):
        (tmp_path / "spec.md").write_text(REQ_SPEC, encoding="utf-8")
        (tmp_path / "test_x.py").write_text(VERB_TESTS + KEYWORD_TESTS, encoding="utf-8")
        res = self._check(tmp_path, self._cfg(require_meaningful_names=True))
        assert res.ok

    def test_missing_spec_treats_no_mapping(self, tmp_path):
        # 无 spec 时命名档仍工作（无 spec 关键词可映射，只认行为动词）
        (tmp_path / "test_x.py").write_text(GENERIC_TESTS, encoding="utf-8")
        res = self._check(tmp_path, self._cfg(require_meaningful_names=True))
        assert not res.ok
        assert {f["reason"] for f in res.evidence["weak_functions"]} == {"generic_name"}

    def test_default_field_false(self):
        assert GateConfig().semantic.test_assertion_quality.require_meaningful_names is False


class TestSkillNaming:
    def test_generic_blocked_then_renamed_passes(self, tmp_path):
        ws = tmp_path
        cfg = GateConfig(spec_file="spec.md")
        opts = cfg.semantic.test_assertion_quality
        opts.enabled = True
        opts.require_meaningful_names = True
        (ws / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
        skill = AntiShortcutSkill(ws, user_request="实现 login 与 fib 函数", config=cfg)

        (ws / "spec.md").write_text(REQ_SPEC, encoding="utf-8")
        assert skill.advance_stage(2)["success"] is True

        (ws / "test_fib.py").write_text(GENERIC_TESTS, encoding="utf-8")
        r = skill.advance_stage(3)
        assert r["success"] is False
        assert "函数名无行为动词" in r["error"]

        (ws / "test_fib.py").write_text(KEYWORD_TESTS, encoding="utf-8")
        r = skill.advance_stage(3)
        assert r["success"] is True and r["stage"] == 3