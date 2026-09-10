# -*- coding: utf-8 -*-
"""五道防线异常分支与边界测试（v0.54.1）。

`test_defense_lines.py` 覆盖五道防线的主干路径；本文件补齐错误处理、
配置边界与较少走到的分支，避免防线代码长期处于「写了但没测」的状态。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import anti_shortcut.defense as defense
from anti_shortcut.config import GateConfig
from anti_shortcut.defense import _common
from anti_shortcut.defense import behavior_audit as ba
from anti_shortcut.defense import dual_review as dr
from anti_shortcut.defense import formal_check as fc
from anti_shortcut.defense import human_review as hr
from anti_shortcut.defense import requirement_template as rt
from anti_shortcut.defense._base import DefenseLine
from anti_shortcut.defense._common import (
    append_jsonl,
    evidence_path,
    load_yaml_or_json,
    read_json,
    read_jsonl,
    redact,
    redact_mapping,
)
from anti_shortcut.defense.behavior_audit import BehaviorAuditLine
from anti_shortcut.defense.dual_review import DualReviewLine
from anti_shortcut.defense.formal_check import FormalCheckLine
from anti_shortcut.defense.human_review import HumanReviewLine, compute_risk_score
from anti_shortcut.defense.requirement_template import (
    RequirementTemplateLine,
    dump_template_skeleton,
)

GOOD_TEMPLATE = {
    "goal": "实现登录鉴权能力",
    "forbidden": ["不允许明文存储密码", "不允许修改数据库表结构"],
    "interfaces": [
        "def login(user: str, pwd: str) -> bool",
        "def lock_status(user: str) -> LockState",
    ],
    "acceptance": ["正确凭据 login 返回 True", "连续 5 次错误后账户进入锁定状态"],
}


class _StubState:
    def __init__(self, user_request: str = "", template=None, fail_set: bool = False) -> None:
        self._ev = {"user_request": user_request, "requirement_template": template}
        self._fail_set = fail_set

    def get_evidence(self, key, default=None):
        return self._ev.get(key, default)

    def set_evidence(self, key, value) -> None:
        if self._fail_set:
            raise RuntimeError("state 不可写")
        self._ev[key] = value


def _cfg(defense_cfg: dict | None = None) -> GateConfig:
    return GateConfig(**({"defense": defense_cfg} if defense_cfg else {}))


class _FakeResp:
    def __init__(self, payload) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


# ---------- 公共工具 ----------


def test_common_read_json_rejects_broken_and_non_mapping(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert read_json(broken) is None
    listed = tmp_path / "list.json"
    listed.write_text("[1, 2]", encoding="utf-8")
    assert read_json(listed) is None
    assert read_json(tmp_path / "absent.json") is None


def test_common_jsonl_roundtrip_skips_blank_and_invalid_lines(tmp_path):
    path = tmp_path / "nested" / "trace.jsonl"
    append_jsonl(path, {"tool": "a"})
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write("{broken\n")
        fh.write("[1, 2]\n")
        fh.write('"scalar"\n')
    append_jsonl(path, {"tool": "b"})
    assert [r["tool"] for r in read_jsonl(path)] == ["a", "b"]
    assert read_jsonl(tmp_path / "missing.jsonl") == []


def test_common_redact_mapping_walks_nested_containers():
    payload = {
        "auth": "Bearer abcdefghijklmnopqrst",
        "items": ["sk-" + "a" * 20, {"password": "password=hunter2"}],
        "count": 3,
        "flag": None,
    }
    out = redact_mapping(payload)
    assert "***" in out["auth"]
    assert "***" in out["items"][0]
    assert "***" in out["items"][1]["password"]
    assert out["count"] == 3 and out["flag"] is None
    assert redact("plain") == "plain"


def test_common_load_yaml_or_json_variants(tmp_path):
    good = tmp_path / "c.json"
    good.write_text('{"formal_constraints": []}', encoding="utf-8")
    assert load_yaml_or_json(good) == {"formal_constraints": []}
    broken = tmp_path / "broken.json"
    broken.write_text("{oops", encoding="utf-8")
    assert load_yaml_or_json(broken) is None
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("a: [1,\n", encoding="utf-8")
    assert load_yaml_or_json(bad_yaml) is None
    assert load_yaml_or_json(tmp_path / "none.yaml") is None


def test_common_stage_label_formats_known_and_unknown():
    assert _common._stage_label(1).startswith("1（")
    assert _common._stage_label(99).startswith("99（")


def test_defense_line_base_contract():
    line = DefenseLine()
    assert line.enabled(_cfg()) is False
    assert line.in_trigger(1, 2) is False
    with pytest.raises(NotImplementedError):
        line.run(Path("."), _cfg(), _StubState(), 1, 2)


def test_run_defense_checks_wraps_line_exception(monkeypatch, tmp_path):
    class Boom(DefenseLine):
        name = "requirement_template"
        trigger = ((1, 2),)

        def run(self, workspace, config, state, from_stage, to_stage):
            raise RuntimeError("boom")

    monkeypatch.setattr(defense, "BUILTIN_DEFENSE_LINES", [Boom()])
    cfg = _cfg({"requirement_template": {"enabled": True}})
    ok, msg, ev = defense.run_defense_checks(tmp_path, cfg, _StubState(), 1, 2)
    assert ok is False and "boom" in msg
    assert ev["defense_checks"][0]["line"] == "requirement_template"


# ---------- 防线 1：需求模板 ----------


def test_validate_requirement_reports_min_item_counts():
    opts = _cfg(
        {
            "requirement_template": {
                "min_forbidden_items": 2,
                "min_interface_items": 2,
                "min_acceptance_items": 2,
            }
        }
    ).defense.requirement_template
    ok, issues, stats = rt.validate_requirement(
        {"goal": "目标", "forbidden": ["a"], "interfaces": ["f"], "acceptance": ["c"]}, opts
    )
    assert not ok and len(issues) == 3
    assert all("条目数" in i for i in issues)
    assert stats["sections"] == {
        "goal": True,
        "forbidden": False,
        "interfaces": False,
        "acceptance": False,
    }


def test_dump_template_skeleton_renders_configured_limits():
    opts = _cfg({"requirement_template": {"goal_max_chars": 123}}).defense.requirement_template
    text = dump_template_skeleton(opts)
    assert "123" in text


def test_line1_invalid_template_from_state_lists_issues(tmp_path):
    cfg = _cfg({"requirement_template": {"enabled": True, "strict": True}})
    bad = {"goal": "", "forbidden": [], "interfaces": [], "acceptance": []}
    r = RequirementTemplateLine().run(tmp_path, cfg, _StubState("自由文本", bad), 1, 2)
    assert not r.ok and "需求模板" in r.message
    assert r.evidence["provided_from"] == "state"


def test_line1_survives_state_write_failure(tmp_path):
    cfg = _cfg({"requirement_template": {"enabled": True}})
    state = _StubState("", GOOD_TEMPLATE, fail_set=True)
    r = RequirementTemplateLine().run(tmp_path, cfg, state, 1, 2)
    assert r.ok


# ---------- 防线 2：双模型交叉复核 ----------


def test_dual_review_load_prompt_variants(tmp_path):
    custom = tmp_path / "custom.txt"
    custom.write_text("CUSTOM", encoding="utf-8")
    assert dr._load_prompt(str(custom), "D") == "CUSTOM"
    assert "覆盖核查" in dr._load_prompt("prompts/coverage_check.txt", "D")
    assert dr._load_prompt(str(tmp_path / "missing.txt"), "D") == "D"
    assert dr._load_prompt("", "D") == "D"


def test_dual_review_extract_json_and_verdict_errors():
    assert dr._extract_json_object('前言 {"verdict": "pass"} 后记') == {"verdict": "pass"}
    with pytest.raises(ValueError):
        dr._extract_json_object("没有 JSON")
    assert dr._coerce_verdict({"verdict": " PASS "}) == "pass"
    with pytest.raises(ValueError):
        dr._coerce_verdict({"verdict": "maybe"})


def test_dual_review_extract_json_rejects_non_mapping(monkeypatch):
    monkeypatch.setattr(dr.json, "loads", lambda text: [1, 2])
    with pytest.raises(ValueError):
        dr._extract_json_object('{"a": 1}')


def test_dual_review_requirement_text_falls_back_to_user_request():
    assert dr._requirement_text("自由文本", "原始需求") == "原始需求"


def test_dual_review_call_model_errors_and_payload(monkeypatch):
    cfg = dr.DualReviewModelOptions(api_key_env="PB_TEST_DR_KEY", endpoint="http://example.invalid/v1")
    monkeypatch.delenv("PB_TEST_DR_KEY", raising=False)
    with pytest.raises(RuntimeError):
        dr._call_model(cfg, [], 1.0)

    monkeypatch.setenv("PB_TEST_DR_KEY", "secret-key")
    seen: dict[str, str] = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization") or ""
        return _FakeResp({"choices": [{"message": {"content": '{"verdict": "pass"}'}}]})

    monkeypatch.setattr(dr.urllib.request, "urlopen", fake_urlopen)
    assert dr._call_model(cfg, [{"role": "user", "content": "hi"}], 5.0) == {"verdict": "pass"}
    assert seen["auth"] == "Bearer secret-key"
    assert seen["url"] == cfg.endpoint

    monkeypatch.setattr(
        dr.urllib.request, "urlopen", lambda req, timeout=None: _FakeResp({"choices": []})
    )
    with pytest.raises(ValueError):
        dr._call_model(cfg, [], 5.0)

    monkeypatch.setattr(
        dr.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResp({"choices": [{"message": {}}]}),
    )
    with pytest.raises(ValueError):
        dr._call_model(cfg, [], 5.0)


def test_line2_missing_spec_is_fail_closed(tmp_path):
    line = DualReviewLine()
    line.client_a = lambda cfg, messages, timeout: {"verdict": "pass"}
    line.client_b = lambda cfg, messages, timeout: {"verdict": "pass"}
    cfg = _cfg({"dual_review": {"enabled": True}})
    r = line.run(tmp_path, cfg, _StubState("x"), 1, 2)
    assert not r.ok and r.evidence["error"] == "missing_spec"


def test_line2_retries_then_succeeds(tmp_path):
    (tmp_path / "spec.md").write_text("spec body", encoding="utf-8")
    attempts = {"b": 0}

    def flaky_b(cfg, messages, timeout):
        attempts["b"] += 1
        if attempts["b"] == 1:
            raise TimeoutError("first attempt")
        return {"verdict": "pass", "tamper": [], "confidence": 0.9, "summary": "ok"}

    line = DualReviewLine()
    line.client_a = lambda cfg, messages, timeout: {
        "verdict": "pass",
        "coverage": [],
        "confidence": 0.9,
        "summary": "ok",
    }
    line.client_b = flaky_b
    cfg = _cfg({"dual_review": {"enabled": True, "max_retries": 2}})
    r = line.run(tmp_path, cfg, _StubState("需求", GOOD_TEMPLATE), 1, 2)
    assert r.ok and attempts["b"] == 2


def test_line2_skips_second_reviewer_when_first_fails(tmp_path):
    (tmp_path / "spec.md").write_text("spec body", encoding="utf-8")
    calls = {"b": 0}

    def boom(cfg, messages, timeout):
        raise RuntimeError("reviewer down")

    def counting_b(cfg, messages, timeout):
        calls["b"] += 1
        return {"verdict": "pass", "confidence": 1.0}

    line = DualReviewLine()
    line.client_a = boom
    line.client_b = counting_b
    cfg = _cfg({"dual_review": {"enabled": True}})
    r = line.run(tmp_path, cfg, _StubState("需求", GOOD_TEMPLATE), 1, 2)
    assert not r.ok and calls["b"] == 0


# ---------- 防线 3：形式化校验 ----------


def test_parse_constraints_reports_every_error_kind():
    assert fc._parse_constraints({"formal_constraints": "nope"})[1]
    _, errors = fc._parse_constraints({"formal_constraints": ["nope"]})
    assert "不是对象" in errors[0]
    _, errors = fc._parse_constraints(
        {"formal_constraints": [{"type": "bogus", "variable": "x", "value": 1}]}
    )
    assert "不支持" in errors[0]
    _, errors = fc._parse_constraints(
        {"formal_constraints": [{"type": "range", "variable": "  ", "value": 1}]}
    )
    assert "缺少 variable" in errors[0]
    _, errors = fc._parse_constraints(
        {"formal_constraints": [{"type": "range", "variable": "x", "operator": "~", "value": 1}]}
    )
    assert "operator" in errors[0]
    _, errors = fc._parse_constraints({"formal_constraints": [{"type": "eq", "variable": "x"}]})
    assert "缺少 value" in errors[0]
    cons, errors = fc._parse_constraints(
        {"constraints": [{"id": "C9", "type": "eq", "variable": "x", "value": 1}]}
    )
    assert not errors and cons[0]["id"] == "C9"


def test_detect_contradictions_covers_all_operators():
    cons, _ = fc._parse_constraints(
        {
            "formal_constraints": [
                {"id": "L", "type": "range", "variable": "v", "operator": ">", "value": 3},
                {"id": "U", "type": "range", "variable": "v", "operator": "<", "value": 5},
                {"id": "E", "type": "eq", "variable": "w", "value": 1},
                {"id": "N", "type": "neq", "variable": "w", "value": 1},
                {"id": "MN", "type": "min_count", "variable": "y", "value": 2},
                {"id": "MX", "type": "max_count", "variable": "y", "value": 4},
                {"id": "EQ", "type": "range", "variable": "z", "operator": "=", "value": 7},
            ]
        }
    )
    msgs = fc.detect_static_contradictions(cons)
    assert any("!=" in m for m in msgs)
    assert not any("区间矛盾" in m for m in msgs)


def test_detect_contradictions_exact_vs_bounds_and_non_integer():
    cons, _ = fc._parse_constraints(
        {
            "formal_constraints": [
                {"id": "A", "type": "range", "variable": "v", "operator": ">=", "value": 10},
                {"id": "B", "type": "eq", "variable": "v", "value": 3},
                {"id": "C", "type": "range", "variable": "v", "operator": "<=", "value": 2},
                {"id": "D", "type": "eq", "variable": "v", "value": 1},
                {"id": "E", "type": "range", "variable": "q", "operator": ">=", "value": "abc"},
            ]
        }
    )
    msgs = fc.detect_static_contradictions(cons)
    assert any("下界" in m for m in msgs)
    assert any("上界" in m for m in msgs)
    assert any("不是整数" in m for m in msgs)
    assert any("区间矛盾" in m for m in msgs)


def test_generate_tla_covers_every_type_and_empty_module():
    cons, _ = fc._parse_constraints(
        {
            "formal_constraints": [
                {"id": "EQ", "type": "eq", "variable": "a", "value": 1},
                {"id": "NE", "type": "neq", "variable": "b", "value": 2},
                {"id": "MI", "type": "min_count", "variable": "c", "value": 3},
                {"id": "MA", "type": "max_count", "variable": "d", "value": 4},
            ]
        }
    )
    text = fc.generate_tla(cons)
    assert "a = 1" in text and "b /= 2" in text
    assert "c >= 3" in text and "d <= 4" in text
    assert "AllConstraints ==" in text
    assert "AllConstraints" not in fc.generate_tla([])


def test_find_tlc_honours_explicit_binary(monkeypatch):
    monkeypatch.setattr(fc.shutil, "which", lambda name: "/usr/local/bin/" + name)
    assert fc._find_tlc("tlc") == "tlc"
    monkeypatch.setattr(fc.shutil, "which", lambda name: None)
    assert fc._find_tlc("tlc") is None
    assert fc._find_tlc(None) is None


def test_run_tlc_executes_subprocess_and_returns_output(tmp_path):
    module = tmp_path / "Constraints.tla"
    module.write_text("---- MODULE Constraints ----\n====\n", encoding="utf-8")
    rc, output = fc._run_tlc(sys.executable, module, 30)
    assert isinstance(rc, int) and isinstance(output, str)


def _constraints_ws(tmp_path, payload: dict) -> None:
    (tmp_path / "constraints.yaml").write_text(json.dumps(payload), encoding="utf-8")


def test_line3_runs_tlc_and_records_returncode(monkeypatch, tmp_path):
    monkeypatch.setattr(fc, "_find_tlc", lambda _bin: "fake-tlc")
    monkeypatch.setattr(fc, "_run_tlc", lambda tlc, path, timeout: (0, "model checking completed"))
    _constraints_ws(
        tmp_path,
        {
            "formal_constraints": [
                {"id": "C1", "type": "range", "variable": "n", "operator": ">=", "value": 1}
            ]
        },
    )
    cfg = _cfg({"formal_check": {"enabled": True}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert r.ok and r.evidence["tlc"]["returncode"] == 0
    assert (evidence_path(tmp_path, cfg, "Constraints.tla")).is_file()


def test_line3_tlc_timeout_and_execution_error(monkeypatch, tmp_path):
    monkeypatch.setattr(fc, "_find_tlc", lambda _bin: "fake-tlc")
    _constraints_ws(
        tmp_path,
        {
            "formal_constraints": [
                {"id": "C1", "type": "range", "variable": "n", "operator": ">=", "value": 1}
            ]
        },
    )
    cfg = _cfg({"formal_check": {"enabled": True}})

    def timeout(tlc, path, seconds):
        raise subprocess.TimeoutExpired(cmd=tlc, timeout=seconds)

    monkeypatch.setattr(fc, "_run_tlc", timeout)
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert not r.ok and r.evidence["tlc"]["returncode"] == 124

    def explodes(tlc, path, seconds):
        raise OSError("cannot exec")

    monkeypatch.setattr(fc, "_run_tlc", explodes)
    r2 = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert not r2.ok and r2.evidence["tlc"]["returncode"] == 125


def test_line3_hand_written_tla_with_failing_tlc(monkeypatch, tmp_path):
    monkeypatch.setattr(fc, "_find_tlc", lambda _bin: "fake-tlc")
    monkeypatch.setattr(fc, "_run_tlc", lambda tlc, path, timeout: (1, "syntax error"))
    (tmp_path / "constraints.tla").write_text("---- MODULE Constraints ----", encoding="utf-8")
    cfg = _cfg({"formal_check": {"enabled": True, "constraints_file": "constraints.tla"}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert not r.ok and "检查失败" in r.message


def test_line3_hand_written_tla_with_passing_tlc(monkeypatch, tmp_path):
    monkeypatch.setattr(fc, "_find_tlc", lambda _bin: "fake-tlc")
    monkeypatch.setattr(fc, "_run_tlc", lambda tlc, path, timeout: (0, "ok"))
    (tmp_path / "constraints.tla").write_text("---- MODULE Constraints ----", encoding="utf-8")
    cfg = _cfg({"formal_check": {"enabled": True, "constraints_file": "constraints.tla"}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert r.ok and r.evidence["hand_written_tla"] is True


def test_line3_require_tlc_with_unsupported_constraints(monkeypatch, tmp_path):
    monkeypatch.setattr(fc, "_find_tlc", lambda _bin: None)
    _constraints_ws(tmp_path, {"formal_constraints": [{"type": "bogus", "variable": "x", "value": 1}]})
    cfg = _cfg({"formal_check": {"enabled": True, "require_tlc": True}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert not r.ok and "TLC" in r.message


def test_line3_unparsable_constraints_file_is_rejected(tmp_path):
    (tmp_path / "constraints.yaml").write_text("not: [valid", encoding="utf-8")
    cfg = _cfg({"formal_check": {"enabled": True}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert not r.ok and r.evidence["dsl_errors"]


def test_line3_non_mapping_constraints_file_is_rejected(tmp_path):
    (tmp_path / "constraints.yaml").write_text("- 1\n- 2\n", encoding="utf-8")
    cfg = _cfg({"formal_check": {"enabled": True}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert not r.ok and r.evidence["dsl_errors"]


# ---------- 防线 4：行为审计 ----------


def test_behavior_audit_record_helpers():
    record = {"tool": "write_file", "args": {"path": "a.py", "content": "x=1", "text": "y"}}
    text = ba._record_text(record, scan_write_content=False)
    assert "x=1" not in text and "path" in text
    assert "x=1" in ba._record_text(record, scan_write_content=True)
    assert ba._command_of({"args": {"command": ["pytest", "-q"]}}) == "pytest -q"
    assert ba._command_of({"args": {"cmd": "npm test"}}) == "npm test"
    assert ba._command_of({"args": "raw"}) == ""
    assert ba._command_of({}) == ""


def test_behavior_audit_extra_patterns_and_invalid_regex(tmp_path):
    cfg = _cfg(
        {
            "behavior_audit": {
                "enabled": True,
                "extra_forbidden_patterns": {"custom": ["rm\\s+-rf", "([unclosed"]},
            }
        }
    )
    trace_path = evidence_path(tmp_path, cfg, "trace.jsonl")
    trace_path.write_text(
        json.dumps({"ts": "t", "tool": "execute_command", "args": {"command": "rm -rf /tmp/x"}, "stage": 3})
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "spec.md").write_text("- 禁止\n- 禁止删除数据\n", encoding="utf-8")
    r = BehaviorAuditLine().run(tmp_path, cfg, _StubState("x"), 5, 6)
    assert not r.ok
    assert any(v["category"] == "custom" for v in r.evidence["violations"])


def test_behavior_audit_without_spec_file_still_scans_trace(tmp_path):
    cfg = _cfg({"behavior_audit": {"enabled": True}})
    trace_path = evidence_path(tmp_path, cfg, "trace.jsonl")
    trace_path.write_text(
        json.dumps({"ts": "t", "tool": "execute_command", "args": {"command": "python -m pytest -q"}})
        + "\n",
        encoding="utf-8",
    )
    r = BehaviorAuditLine().run(tmp_path, cfg, _StubState("x", GOOD_TEMPLATE), 5, 6)
    assert r.ok and r.evidence["seen_test_command"] is True


# ---------- 防线 5：概率人工复核 ----------


def test_spec_summary_without_spec_file(tmp_path):
    assert hr._spec_summary(tmp_path, _cfg()) == "(无 spec)"


def test_compute_risk_score_uses_dual_confidence_and_trace(tmp_path):
    cfg = _cfg({"dual_review": {"enabled": True}, "behavior_audit": {"enabled": True}})
    evidence_path(tmp_path, cfg, "dual_review.json").write_text(
        json.dumps({"parsed": {"reviewer_a": {"confidence": 0.5}, "reviewer_b": {"confidence": 0.7}}}),
        encoding="utf-8",
    )
    evidence_path(tmp_path, cfg, "behavior_diff.json").write_text(
        json.dumps({"violations": [{"category": "db_schema"}]}), encoding="utf-8"
    )
    trace = [
        {"tool": "write_file", "args": {"path": "a.py"}},
        {"tool": "write_file", "args": {"file": "b.py"}},
        {"tool": "read_file", "args": {"path": "c.py"}},
        {"tool": "write_file", "args": "solid"},
    ]
    evidence_path(tmp_path, cfg, "trace.jsonl").write_text(
        "\n".join(json.dumps(t) for t in trace) + "\n", encoding="utf-8"
    )
    score, parts = compute_risk_score(tmp_path, cfg, _StubState("需求", None))
    assert parts["dual_confidence"] == 0.6
    assert parts["audit_violations"] == 1
    assert parts["files_touched"] == 2
    assert 0 <= score <= 100


def test_line5_auto_approves_below_threshold(tmp_path):
    cfg = _cfg(
        {
            "human_review": {
                "enabled": True,
                "force_above_score": 100,
                "auto_approve_below_score": 100,
            }
        }
    )
    r = HumanReviewLine().run(tmp_path, cfg, _StubState("短需求", None), 5, 6)
    assert r.ok and r.evidence["probability"] == 0.0


def test_line5_nondeterministic_sampling_branch(tmp_path, monkeypatch):
    class _ZeroRandom:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def random(self) -> float:
            return 0.0

    monkeypatch.setattr(hr.random, "Random", _ZeroRandom)
    cfg = _cfg(
        {
            "human_review": {
                "enabled": True,
                "deterministic": False,
                "force_above_score": 100,
                "auto_approve_below_score": 0,
                "sample_rate": 1.0,
            }
        }
    )
    r = HumanReviewLine().run(tmp_path, cfg, _StubState("需求", None), 5, 6)
    assert r.evidence["sampled"] is True


def test_load_approvals_tolerates_non_mapping_payload(tmp_path):
    cfg = _cfg()
    path = evidence_path(tmp_path, cfg, "human_review_approvals.json")
    path.write_text("[1, 2]", encoding="utf-8")
    assert hr._load_approvals(path) == []
