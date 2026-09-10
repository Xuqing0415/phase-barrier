# -*- coding: utf-8 -*-
"""五道防线测试（v0.52.0）。

覆盖：
- 配置模型默认值 / 校验（config.defense.* 全部默认关闭）；
- 防线 1：需求模板校验与 Skill 端到端（strict 拒绝自由文本 -> 补模板放行）；
- 防线 2：双模型交叉复核（双 pass / 单侧 fail / 调用失败 fail-closed /
  置信度不足 / 证据落盘）；
- 防线 3：形式化校验（静态区间矛盾 / 无约束降级 / require_tlc 缺工具拒绝）；
- 防线 4：行为审计（trace 命中 ALTER TABLE / 联网 -> 拒绝交付，
  干净轨迹 + 测试命令 -> 放行；缺测试命令默认仅警告）；
- 防线 5：概率人工复核（高风险必抽 -> 请求落盘 -> approve 放行，低风险自动放行）；
- CLI：init-requirement / review-approve。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import anti_shortcut.defense as defense
from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig
from anti_shortcut.defense._common import evidence_path
from anti_shortcut.defense.behavior_audit import BehaviorAuditLine
from anti_shortcut.defense.dual_review import DualReviewLine
from anti_shortcut.defense.formal_check import (
    FormalCheckLine,
    detect_static_contradictions,
    generate_tla,
)
from anti_shortcut.defense.human_review import HumanReviewLine, approve_request
from anti_shortcut.defense.requirement_template import (
    RequirementTemplateLine,
    validate_requirement,
)

GOOD_TEMPLATE = {
    "goal": "实现登录鉴权能力，凭据正确放行，连续 5 次失败锁定 30 分钟",
    "forbidden": ["不允许明文存储密码", "不允许修改数据库表结构"],
    "interfaces": [
        "def login(user: str, pwd: str) -> bool",
        "def lock_status(user: str) -> LockState",
    ],
    "acceptance": [
        "正确凭据 login 返回 True",
        "连续 5 次错误后账户进入锁定状态",
    ],
}

SPEC_LOGIN = """# 登录鉴权模块 Spec

## 需求分析
用户需要登录鉴权能力 login(user, pwd)，凭据正确返回 True；错误次数超限锁定账户。
- 禁止修改数据库表结构、禁止删除数据库表

## 设计方案
采用 HMAC-SHA256 存储密码摘要而非明文，引入失败计数器并持久化到 state_store，
登录接口使用固定时间比较防止时序侧信道。

## 接口定义
- def login(user: str, pwd: str) -> bool
- def lock_status(user: str) -> LockState
- 输入 user/pwd 为字符串；输出 bool 或 LockState
"""


class _StubState:
    def __init__(self, user_request: str = "", template=None) -> None:
        self._ev = {
            "user_request": user_request,
            "requirement_template": template,
        }

    def get_evidence(self, key: str, default=None):
        return self._ev.get(key, default)

    def set_evidence(self, key: str, value) -> None:
        self._ev[key] = value


def _cfg(defense_cfg: dict | None = None) -> GateConfig:
    return GateConfig(**({"defense": defense_cfg} if defense_cfg else {}))


# ---------- 配置 ----------


def test_defense_config_defaults_off():
    cfg = _cfg()
    d = cfg.defense
    assert d.requirement_template.enabled is False
    assert d.dual_review.enabled is False
    assert d.formal_check.enabled is False
    assert d.behavior_audit.enabled is False
    assert d.human_review.enabled is False


def test_defense_config_nested_load_and_validation():
    cfg = _cfg(
        {
            "requirement_template": {"enabled": True, "strict": True, "goal_max_chars": 120},
            "human_review": {"sample_rate": 0.5, "force_above_score": 90},
        }
    )
    assert cfg.defense.requirement_template.strict is True
    assert cfg.defense.requirement_template.goal_max_chars == 120
    assert cfg.defense.human_review.sample_rate == 0.5
    with pytest.raises(ValidationError):
        _cfg({"human_review": {"sample_rate": 1.5}})
    with pytest.raises(ValidationError):
        _cfg({"human_review": {"force_above_score": 120}})


def test_run_defense_checks_skips_when_disabled():
    ws = Path(".")
    state = _StubState()
    ok, msg, ev = defense.run_defense_checks(ws, _cfg(), state, 1, 2)
    assert ok and msg == "" and ev == {}


# ---------- 防线 1：需求模板 ----------


def test_validate_requirement_ok_and_issues():
    opts = _cfg().defense.requirement_template
    ok, issues, stats = validate_requirement(GOOD_TEMPLATE, opts)
    assert ok and not issues
    assert stats["sections"] == {
        "goal": True,
        "forbidden": True,
        "interfaces": True,
        "acceptance": True,
    }
    ok2, issues2, _ = validate_requirement({"goal": ""}, opts)
    assert not ok2 and any("目标" in i for i in issues2)
    long_goal = dict(GOOD_TEMPLATE, goal="长" * 300)
    ok3, issues3, _ = validate_requirement(long_goal, opts)
    assert not ok3 and any("超过" in i for i in issues3)
    no_forbid = dict(GOOD_TEMPLATE, forbidden=[])
    ok4, issues4, _ = validate_requirement(no_forbid, opts)
    assert not ok4 and any("禁止行为清单" in i for i in issues4)


def test_line1_strict_rejects_free_text_then_template_passes(tmp_path):
    cfg = _cfg({"requirement_template": {"enabled": True, "strict": True}})
    ws = tmp_path
    state = _StubState(user_request="实现登录功能")
    r = RequirementTemplateLine().run(ws, cfg, state, 1, 2)
    assert not r.ok and "缺失" in r.message
    (ws / "requirement.yaml").write_text(json.dumps(GOOD_TEMPLATE, ensure_ascii=False), encoding="utf-8")
    r2 = RequirementTemplateLine().run(ws, cfg, state, 1, 2)
    assert r2.ok
    assert state.get_evidence("requirement_template") == GOOD_TEMPLATE


def test_line1_non_strict_warns_but_allows(tmp_path):
    cfg = _cfg({"requirement_template": {"enabled": True, "strict": False}})
    r = RequirementTemplateLine().run(tmp_path, cfg, _StubState("随便写"), 1, 2)
    assert r.ok and r.evidence.get("strict") is False


def test_skill_e2e_line1_strict(tmp_path):
    ws = tmp_path
    (ws / "pytest.ini").write_text("[pytest]\ntestpaths=.\n", encoding="utf-8")
    cfg = _cfg({"requirement_template": {"enabled": True, "strict": True}})
    skill = AntiShortcutSkill(ws, config=cfg, user_request="实现登录功能")
    (ws / "spec.md").write_text(SPEC_LOGIN, encoding="utf-8")
    r = skill.advance_stage(2)
    assert not r["success"] and "需求模板" in r["error"]
    (ws / "requirement.yaml").write_text(json.dumps(GOOD_TEMPLATE, ensure_ascii=False), encoding="utf-8")
    r2 = skill.advance_stage(2)
    assert r2["success"] and skill.current_stage == 2


# ---------- 防线 2：双模型交叉复核 ----------


def _review_client(verdict: str = "pass", confidence: float = 0.95, kind: str = "a", extra=None):
    def client(cfg, messages, timeout):
        if kind == "a":
            return {
                "verdict": verdict,
                "coverage": [{"item": "登录", "status": "covered", "reason": "ok"}],
                "confidence": confidence,
                "summary": "覆盖完整",
            }
        return {
            "verdict": verdict,
            "tamper": [{"type": "removed_constraint", "requirement": "锁定", "spec_text": "-", "reason": "缺失"}]
            if verdict == "fail"
            else [],
            "confidence": confidence,
            "summary": "无篡改" if verdict == "pass" else "约束被删",
        }

    return client


def _dual_ws(tmp_path) -> Path:
    (tmp_path / "spec.md").write_text(SPEC_LOGIN, encoding="utf-8")
    return tmp_path


def test_line2_both_pass(tmp_path):
    line = DualReviewLine()
    line.client_a = _review_client("pass", kind="a")
    line.client_b = _review_client("pass", kind="b")
    cfg = _cfg({"dual_review": {"enabled": True}})
    state = _StubState("实现登录", GOOD_TEMPLATE)
    r = line.run(_dual_ws(tmp_path), cfg, state, 1, 2)
    assert r.ok
    ev_file = evidence_path(tmp_path, cfg, "dual_review.json")
    assert ev_file.is_file()


def test_line2_single_fail_blocks(tmp_path):
    for kind, verdict in (("a", "fail"), ("b", "fail")):
        line = DualReviewLine()
        line.client_a = _review_client("pass", kind="a")
        line.client_b = _review_client("pass", kind="b")
        if kind == "a":
            line.client_a = _review_client("fail", kind="a")
        else:
            line.client_b = _review_client("fail", kind="b")
        cfg = _cfg({"dual_review": {"enabled": True}})
        r = line.run(_dual_ws(tmp_path), cfg, _StubState("实现登录", GOOD_TEMPLATE), 1, 2)
        assert not r.ok and ("reviewer_a]" in r.message or "reviewer_b]" in r.message)


def test_line2_call_failure_fail_closed(tmp_path):
    line = DualReviewLine()
    line.client_a = _review_client("pass", kind="a")

    def boom(cfg, messages, timeout):
        raise TimeoutError("timeout")

    line.client_b = boom
    cfg = _cfg({"dual_review": {"enabled": True}})
    r = line.run(_dual_ws(tmp_path), cfg, _StubState("x", GOOD_TEMPLATE), 1, 2)
    assert not r.ok and "reviewer_b" in r.message
    cfg2 = _cfg({"dual_review": {"enabled": True, "fail_closed": False}})
    r2 = line.run(_dual_ws(tmp_path), cfg2, _StubState("x", GOOD_TEMPLATE), 1, 2)
    assert r2.ok and "降级" in r2.message


def test_line2_low_confidence_blocks(tmp_path):
    line = DualReviewLine()
    line.client_a = _review_client("pass", confidence=0.4, kind="a")
    line.client_b = _review_client("pass", confidence=0.9, kind="b")
    cfg = _cfg({"dual_review": {"enabled": True, "min_confidence": 0.8}})
    r = line.run(_dual_ws(tmp_path), cfg, _StubState("x", GOOD_TEMPLATE), 1, 2)
    assert not r.ok and "置信度" in r.message


# ---------- 防线 3：形式化校验 ----------


CONTRADICT_CONSTRAINTS = {
    "formal_constraints": [
        {"id": "C1", "type": "range", "variable": "password_len", "operator": ">=", "value": 8},
        {"id": "C2", "type": "range", "variable": "password_len", "operator": "<=", "value": 6},
    ]
}
CONSISTENT_CONSTRAINTS = {
    "formal_constraints": [
        {"id": "C1", "type": "range", "variable": "password_len", "operator": ">=", "value": 8},
        {"id": "C2", "type": "max_count", "variable": "active_sessions", "value": 3},
    ]
}


def test_detect_static_contradictions():
    import yaml

    from anti_shortcut.defense.formal_check import _parse_constraints

    cons, _ = _parse_constraints(CONTRADICT_CONSTRAINTS)
    assert detect_static_contradictions(cons)
    cons2, _ = _parse_constraints(CONSISTENT_CONSTRAINTS)
    assert not detect_static_contradictions(cons2)
    generated = generate_tla(cons2)
    assert "password_len >= 8" in generated and "active_sessions <= 3" in generated


def test_line3_contradiction_rejected(tmp_path):
    (tmp_path / "constraints.yaml").write_text(json.dumps(CONTRADICT_CONSTRAINTS), encoding="utf-8")
    cfg = _cfg({"formal_check": {"enabled": True}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert not r.ok and "矛盾" in r.message


def test_line3_consistent_passes_without_tlc(tmp_path):
    (tmp_path / "constraints.yaml").write_text(json.dumps(CONSISTENT_CONSTRAINTS), encoding="utf-8")
    cfg = _cfg({"formal_check": {"enabled": True}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert r.ok


def test_line3_no_constraints_downgrades(tmp_path):
    cfg = _cfg({"formal_check": {"enabled": True}})
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert r.ok and r.evidence.get("mode") == "downgraded"


def test_line3_hand_tla_require_tlc_fails_without_tool(tmp_path):
    (tmp_path / "constraints.tla").write_text("---- MODULE Constraints ====", encoding="utf-8")
    cfg = _cfg(
        {
            "formal_check": {
                "enabled": True,
                "require_tlc": True,
                "tlc_bin": "no-such-tlc",
                "constraints_file": "constraints.tla",
            }
        }
    )
    r = FormalCheckLine().run(tmp_path, cfg, _StubState(), 1, 2)
    assert not r.ok and "TLC" in r.message


# ---------- 防线 4：行为审计 ----------


def _trace_ws(tmp_path, spec: str = SPEC_LOGIN, trace: list[dict] | None = None):
    cfg = _cfg({"behavior_audit": {"enabled": True}})
    (tmp_path / "spec.md").write_text(spec, encoding="utf-8")
    if trace:
        path = evidence_path(tmp_path, cfg, "trace.jsonl")
        path.write_text(
            "\n".join(json.dumps(t, ensure_ascii=False) for t in trace) + "\n",
            encoding="utf-8",
        )
    return cfg


def test_line4_forbidden_alter_table_rejected(tmp_path):
    trace = [
        {"ts": "t1", "tool": "execute_command", "args": {"command": "python -m pytest -q"}, "stage": 4},
        {"ts": "t2", "tool": "execute_command", "args": {"command": "sqlite3 db.sqlite 'ALTER TABLE users ADD COLUMN x'"}, "stage": 3},
    ]
    cfg = _trace_ws(tmp_path, trace=trace)
    r = BehaviorAuditLine().run(tmp_path, cfg, _StubState("登录", GOOD_TEMPLATE), 5, 6)
    assert not r.ok and "db_schema" in r.message


def test_line4_forbidden_network_write_rejected(tmp_path):
    spec = SPEC_LOGIN.replace("## 设计方案", "## 约束\n- 禁止联网、禁止网络访问\n\n## 设计方案")
    trace = [
        {"ts": "t", "tool": "write_file", "args": {"path": "src/impl.py", "content": "import urllib.request\nurllib.request.urlopen('http://x')"}, "stage": 3},
        {"ts": "t2", "tool": "execute_command", "args": {"command": "python -m pytest -q"}, "stage": 4},
    ]
    cfg = _trace_ws(tmp_path, spec=spec, trace=trace)
    r = BehaviorAuditLine().run(tmp_path, cfg, _StubState("登录", GOOD_TEMPLATE), 5, 6)
    assert not r.ok and "network" in r.message


def test_line4_clean_trace_passes_with_test(tmp_path):
    trace = [
        {"ts": "t1", "tool": "write_file", "args": {"path": "src/fib.py", "content": "def fib(n): return n"}, "stage": 3},
        {"ts": "t2", "tool": "execute_command", "args": {"command": "python -m pytest -q"}, "stage": 4},
    ]
    cfg = _trace_ws(tmp_path, trace=trace)
    r = BehaviorAuditLine().run(tmp_path, cfg, _StubState("登录", GOOD_TEMPLATE), 5, 6)
    assert r.ok and r.evidence["seen_test_command"] is True


def test_line4_missing_test_command_warns_or_denies(tmp_path):
    cfg = _trace_ws(tmp_path, trace=[{"ts": "t", "tool": "write_file", "args": {"path": "a.py", "content": "x=1"}, "stage": 3}])
    r = BehaviorAuditLine().run(tmp_path, cfg, _StubState("x"), 5, 6)
    assert r.ok and r.evidence["missing_test_command"] is True
    cfg2 = _cfg({"behavior_audit": {"enabled": True, "deny_missing_test_command": True}})
    (tmp_path / "spec.md").write_text(SPEC_LOGIN, encoding="utf-8")
    r2 = BehaviorAuditLine().run(tmp_path, cfg2, _StubState("x"), 5, 6)
    assert not r2.ok and "测试命令" in r2.message


def test_skill_e2e_behavior_audit_trace_via_wrappers(tmp_path):
    """端到端：经包装工具写入含联网代码的实现，交付前被防线 4 拦截。"""
    ws = tmp_path
    (ws / "pytest.ini").write_text("[pytest]\ntestpaths=.\n", encoding="utf-8")
    spec = """# 斐波那契函数 Spec

## 需求分析
需要一个函数 fib(n)，计算斐波那契数列第 n 项。F(0)=0, F(1)=1。

## 设计方案
采用迭代法，滚动维护前两项，时间复杂度 O(n)。

## 接口定义
def fib(n: int) -> int

## 约束
- 禁止联网、禁止网络访问
"""
    (ws / "spec.md").write_text(spec, encoding="utf-8")
    cfg = _cfg(
        {
            "behavior_audit": {"enabled": True},
            "requirement_template": {"enabled": True, "strict": True},
        }
    )
    skill = AntiShortcutSkill(ws, config=cfg, user_request="实现 fib 函数")
    (ws / "requirement.yaml").write_text(
        json.dumps(
            {
                "goal": "实现斐波那契函数",
                "forbidden": ["不允许联网"],
                "interfaces": ["def fib(n:int)->int", "def fib_fast(n:int)->int"],
                "acceptance": ["fib(10)==55", "负数抛 ValueError"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    tools = skill.install({"write_file": _write(ws), "execute_command": _exec(ws)})
    assert skill.advance_stage(2)["success"]
    good_tests = '''"""测试用例"""
import pytest
from fib import fib


def test_base_cases():
    assert fib(0) == 0
    assert fib(1) == 1


def test_known_value():
    assert fib(10) == 55
'''
    tools["write_file"]("tests/test_fib.py", good_tests)
    assert skill.advance_stage(3)["success"]
    good_impl = '''def fib(n):
    if n < 0:
        raise ValueError("n must be >= 0")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
'''
    tampered = good_impl + "\nimport urllib.request  # 后续接入外部服务时会用到\n"
    tools["write_file"]("fib.py", tampered)
    assert skill.advance_stage(4)["success"]
    tools["execute_command"]("python -m pytest -q")
    # 阶段 4 测试通过后请求进入 5，内部按“跳修复”改投阶段 6（此时防线 4/5 触发）
    r = skill.advance_stage(5)
    assert not r["success"] and "行为审计" in r["error"]


def _write(ws: Path):
    def write_file(path, content):
        p = ws / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True}

    return write_file


def _exec(ws: Path):
    import subprocess

    def execute_command(command):
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=ws,
        )
        return {"exit_code": proc.returncode, "output": (proc.stdout or "") + (proc.stderr or "")}

    return execute_command


# ---------- 防线 5：概率人工复核 ----------


def test_line5_high_risk_forced_sampling_and_approval(tmp_path):
    ws = tmp_path
    (ws / "spec.md").write_text(SPEC_LOGIN, encoding="utf-8")
    cfg = _cfg({"human_review": {"enabled": True, "force_above_score": 0, "sample_rate": 0.5}})
    state = _StubState("实现登录鉴权能力" * 10, GOOD_TEMPLATE)
    r = HumanReviewLine().run(ws, cfg, state, 5, 6)
    assert not r.ok and r.request_id
    req_file = evidence_path(ws, cfg, "human_review_request.json")
    assert req_file.is_file()
    payload = json.loads(req_file.read_text(encoding="utf-8"))
    assert payload["request_id"] == r.request_id
    approve_request(ws, cfg, r.request_id, reason="人工已核对")
    r2 = HumanReviewLine().run(ws, cfg, state, 5, 6)
    assert r2.ok and r2.evidence.get("approved") is True


def test_line5_low_risk_auto_approves(tmp_path):
    ws = tmp_path
    # 注意：抽样种子取自 request_id = hash(workspace|需求|阶段)，而 tmp_path 每次运行
    # 都不同；若沿用默认 auto_approve_below_score=20（本用例实际风险分 35），断言会依赖
    # 伪随机数，约 12% 概率随机失败。这里显式把阈值抬到风险分之上，保证确定性。
    cfg = _cfg({"human_review": {"enabled": True, "auto_approve_below_score": 100}})
    state = _StubState("", {})
    r = HumanReviewLine().run(ws, cfg, state, 5, 6)
    assert r.ok and r.evidence["sampled"] is False


def test_compute_risk_score_penalizes_missing_formal(tmp_path):
    from anti_shortcut.defense.human_review import compute_risk_score

    ws = tmp_path
    cfg = _cfg()
    state = _StubState("很长" * 50, GOOD_TEMPLATE)
    score, parts = compute_risk_score(ws, cfg, state)
    assert 0 <= score <= 100
    assert parts["formal_enabled"] is False and parts["formal_penalty"] == 15


# ---------- CLI ----------


def test_cli_init_requirement_example(tmp_path, capsys):
    from anti_shortcut.__main__ import main

    rc = main(["init-requirement", "--workspace", str(tmp_path), "--example"])
    assert rc == 0
    assert (tmp_path / "requirement.yaml").is_file()


def test_cli_init_requirement_flags(tmp_path, capsys):
    from anti_shortcut.__main__ import main

    import yaml

    rc = main(
        [
            "init-requirement",
            "--workspace", str(tmp_path),
            "--goal", "登录鉴权",
            "--forbidden", "禁止明文密码;禁止联网",
            "--interfaces", "def login(user,pwd)->bool;def lock_status(user)->LockState",
            "--acceptance", "正确凭据返回True;连续5次错误锁定",
        ]
    )
    assert rc == 0
    data = yaml.safe_load((tmp_path / "requirement.yaml").read_text(encoding="utf-8"))
    assert "goal" in data and len(data["forbidden"]) == 2


def test_cli_review_approve_requires_request(tmp_path, capsys):
    from anti_shortcut.__main__ import main

    rc = main(["review-approve", "--workspace", str(tmp_path), "--request-id", "abc123"])
    assert rc == 0
    cfg = _cfg()
    approvals = json.loads(evidence_path(tmp_path, cfg, "human_review_approvals.json").read_text(encoding="utf-8"))
    assert approvals["approvals"][0]["request_id"] == "abc123"
