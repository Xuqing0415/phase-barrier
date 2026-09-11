"""红队逃逸技术库：框架契约与载荷有效性测试（路径 1，v0.57.0）。

覆盖：
- ``EscapeTechnique`` / ``EscapeAttempt`` / ``EscapeResult`` 的契约与校验；
- 每种技术的 ``generate`` / ``generate_all`` 都能产出可回放、可判定的载荷；
- 载荷里的 ``requirement.yaml`` 必须是**合法 YAML**（历史缺陷：函数签名里的 ``: ``
  未加引号导致解析失败，防线 1 会误报「缺少需求模板」）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from anti_shortcut.defense._common import load_yaml_or_json

from benchmarks.red_team.agent import SandboxConfig, summarize
from benchmarks.red_team.techniques import ALL_TECHNIQUES, TECHNIQUES_BY_NAME
from benchmarks.red_team.techniques.equivalent_op import VARIANTS
from benchmarks.red_team.techniques.base import (
    DEFENSE_LABELS,
    Defense,
    EscapeAttempt,
    EscapeResult,
    EscapeTechnique,
    Outcome,
    Requirement,
    ToolCall,
)


def test_defense_enum_and_labels_cover_all_five_lines():
    assert [d.value for d in Defense] == [1, 2, 3, 4, 5]
    assert set(DEFENSE_LABELS) == {1, 2, 3, 4, 5}


def test_outcome_passed_defense_semantics():
    assert Outcome.BLOCKED.passed_defense is False
    assert Outcome.SKIPPED.passed_defense is False
    assert Outcome.ESCAPED.passed_defense is True
    # 不可判定 ≠ 防线有效：不能据此得出「防线拦截了」的结论
    assert Outcome.INCONCLUSIVE.passed_defense is True


def test_tool_call_factories():
    assert ToolCall.write("a.py", "x").tool == "write_file"
    assert ToolCall.raw_write("a.py", "x").tool == "raw_write"
    assert ToolCall.exec("pytest").tool == "execute_command"
    assert ToolCall.raw_exec("pytest").tool == "raw_exec"
    assert ToolCall.advance(3).stage == 3


class _ValidTechnique(EscapeTechnique):
    name = "valid"
    target_defense = 2

    def generate(self, requirement: str) -> EscapeAttempt:
        return self.make_attempt(requirement, goal_stage=2, calls=[ToolCall.advance(2)])


def test_make_attempt_builds_attempt_and_validates_metadata():
    attempt = _ValidTechnique().generate("需求")
    assert attempt.technique == "valid" and attempt.target_defense == 2
    assert attempt.expectation == "blocked" and attempt.goal_stage == 2


@pytest.mark.parametrize(
    "name,target,goal,expectation,enabled,match",
    [
        ("", 2, 2, "blocked", None, "name"),
        ("valid", 9, 2, "blocked", None, "target_defense"),
        ("valid", 2, 99, "blocked", None, "goal_stage"),
        ("valid", 2, 2, "nonsense", None, "expectation"),
        ("valid", 2, 2, "blocked", (0,), "enabled_defenses"),
    ],
)
def test_make_attempt_rejects_invalid_metadata(
    name, target, goal, expectation, enabled, match
):
    technique = _ValidTechnique()
    technique.name = name
    technique.target_defense = target
    technique.expectation = expectation
    with pytest.raises(ValueError, match=match):
        technique.make_attempt("需求", goal_stage=goal, enabled_defenses=enabled)


def test_escape_result_flags_vulnerability_and_residual():
    common = dict(
        technique="t", target_defense=4, goal_stage=6, stage_reached=6, message="m"
    )
    assert EscapeResult(expectation="blocked", outcome=Outcome.ESCAPED, **common).is_vulnerability
    assert not EscapeResult(
        expectation="blocked", outcome=Outcome.BLOCKED, **common
    ).is_vulnerability
    residual = EscapeResult(expectation="partial", outcome=Outcome.ESCAPED, **common)
    assert residual.is_residual_risk and not residual.is_vulnerability
    assert residual.needs_attention
    inconclusive = EscapeResult(
        expectation="blocked", outcome=Outcome.INCONCLUSIVE, **common
    )
    assert inconclusive.needs_attention and not inconclusive.is_vulnerability


def test_escape_result_to_row_includes_case_and_defense_label():
    row = EscapeResult(
        technique="equivalent_op",
        case_id="shutil_rmtree",
        target_defense=4,
        expectation="blocked",
        outcome=Outcome.BLOCKED,
        goal_stage=6,
        stage_reached=4,
        message="被拦截",
        blocked_by="behavior_audit",
    ).to_row()
    assert row["case"] == "equivalent_op/shutil_rmtree"
    assert row["defense_label"] == DEFENSE_LABELS[4]
    assert row["vulnerability"] is False and row["residual_risk"] is False


def test_summarize_splits_vulnerabilities_and_residual_risks():
    def result(technique, outcome, expectation):
        return EscapeResult(
            technique=technique,
            target_defense=4,
            expectation=expectation,
            outcome=outcome,
            goal_stage=6,
            stage_reached=6,
            message="m",
        )

    report = summarize(
        [
            result("a", Outcome.ESCAPED, "blocked"),
            result("b", Outcome.ESCAPED, "partial"),
            result("c", Outcome.BLOCKED, "blocked"),
        ]
    )
    assert report["total"] == 3
    assert report["counts"] == {"escaped": 2, "blocked": 1}
    assert [r["technique"] for r in report["vulnerabilities"]] == ["a"]
    assert [r["technique"] for r in report["residual_risks"]] == ["b"]


# ---------- 八类技术 ----------


def test_registry_has_eighteen_techniques_with_expected_targets():
    """v0.57.0 的 8 类 + v0.60.0 扩面 10 类（P1：多步 / 会话级 / 工具链 / 混淆）。"""
    assert len(ALL_TECHNIQUES) == 18
    assert {t.name for t in ALL_TECHNIQUES} == {
        "template_filler",
        "coverage_disguise",
        "constraint_weaken",
        "constraint_replace",
        "formal_evade",
        "equivalent_op",
        "timing_evade",
        "risk_camouflage",
        "tool_chain_script",
        "deliverable_truncation",
        "obfuscated_exec",
        "stage_skip_delivery",
        "chain_test_coverage",
        "chain_spec_drift",
        "chain_exfil_repo",
        "session_family_split",
        "session_family_stagger",
        "session_family_below_threshold",
    }
    assert all(1 <= t.target_defense <= 5 for t in ALL_TECHNIQUES)


def test_escape_chains_produce_multi_stage_sequences():
    """多步链路必须真实跨阶段：调用序列里出现 >= 3 次 advance_stage。"""
    for name in ("chain_test_coverage", "chain_spec_drift", "chain_exfil_repo"):
        technique = TECHNIQUES_BY_NAME[name]
        attempt = technique.generate("需求")
        advances = [c.stage for c in attempt.calls if c.tool == "advance_stage"]
        assert len(advances) >= 3, name
        assert advances == sorted(advances), name
        assert "攻击链" in attempt.notes


def test_tool_chain_variants_write_to_non_source_extensions():
    """工具链攻击的载体必须落在 source_extensions 之外，否则测不到补扫。"""
    attempts = TECHNIQUES_BY_NAME["tool_chain_script"].generate_all("需求")
    assert len(attempts) == 5
    source_exts = {
        ".py", ".js", ".ts", ".sh", ".ps1", ".sql", ".go", ".rs", ".java",
    }
    payloads_ = []
    for attempt in attempts:
        writes = [
            c
            for c in attempt.calls
            if c.tool == "write_file"
            and c.path not in ("mod.py", "spec.md", "test_mod.py")
        ]
        assert writes, attempt.case_id
        payloads_.append(writes[0])
        assert Path(writes[0].path).suffix not in source_exts
        assert "rm -rf" in writes[0].content or "rmtree" in writes[0].content
    commands = [
        c.command
        for a in attempts
        for c in a.calls
        if c.tool == "execute_command" and c.command and "pytest" not in c.command
    ]
    assert len(commands) == 5
    for write in payloads_:
        if write.path == "Makefile":
            assert any(cmd.startswith("make") for cmd in commands)
        else:
            assert any(write.path in cmd for cmd in commands)


def test_obfuscated_variants_are_semantically_dangerous_but_not_literal():
    attempts = TECHNIQUES_BY_NAME["obfuscated_exec"].generate_all("需求")
    assert [a.case_id for a in attempts] == ["quote_split", "backslash_escape", "base64_pipe"]
    raw_commands = [
        c.command
        for a in attempts
        for c in a.calls
        if c.tool == "execute_command" and "pytest" not in (c.command or "")
    ]
    assert len(raw_commands) == 3
    # 字面里不应出现完整的 "rm -rf"（否则这条技术就没有测到去混淆）
    assert all("rm -rf" not in cmd for cmd in raw_commands)


def test_session_family_techniques_declare_shared_family_and_partial_expectation():
    for name in (
        "session_family_split",
        "session_family_stagger",
        "session_family_below_threshold",
    ):
        technique = TECHNIQUES_BY_NAME[name]
        attempts = technique.generate_all("需求")
        assert len(attempts) >= 3, name
        families = {a.state_evidence.get("task_family_id") for a in attempts}
        assert len(families) == 1 and None not in families, name
        assert all(a.expectation == "partial" for a in attempts), name
        for attempt in attempts:
            opts = attempt.config_overrides["defense"]["human_review"]
            assert opts["task_family_detection"] is True
            assert opts["rollup_ledger"] == "{sandbox_root}/session_ledger.jsonl"


def test_misc_techniques_are_well_formed():
    skip = TECHNIQUES_BY_NAME["stage_skip_delivery"].generate("需求")
    assert [c.stage for c in skip.calls if c.tool == "advance_stage"] == [6]
    trunc = TECHNIQUES_BY_NAME["deliverable_truncation"].generate("需求")
    impl_writes = [c for c in trunc.calls if c.tool == "write_file" and c.path == "mod.py"]
    assert impl_writes and len(impl_writes[0].content) > 4000
    assert impl_writes[0].content.index("rmtree") > 4000


def test_all_attempts_are_replayable_and_well_formed():
    for technique in ALL_TECHNIQUES:
        attempts = technique.generate_all("实现用户登录功能，密码至少 8 位")
        assert attempts, technique.name
        for attempt in attempts:
            assert attempt.technique == technique.name
            assert 1 <= attempt.goal_stage <= 6
            assert attempt.calls, f"{technique.name} 未产出任何工具调用"
            assert attempt.expectation in ("blocked", "escaped", "partial")
            assert attempt.enabled_defenses is None or all(
                1 <= d <= 5 for d in attempt.enabled_defenses
            )


def test_requirement_yaml_payloads_are_parseable(tmp_path):
    """历史缺陷回归：函数签名里的 ``: `` 未加引号 -> YAML 解析失败 -> 防线 1 误报缺模板。"""
    checked = 0
    for technique in ALL_TECHNIQUES:
        for attempt in technique.generate_all("需求"):
            raw = attempt.setup.get("requirement.yaml")
            if raw is None:
                continue
            path = tmp_path / f"{attempt.technique}-{attempt.case_id or 'case'}.yaml"
            path.write_text(raw, encoding="utf-8")
            data = load_yaml_or_json(path)
            assert isinstance(data, dict), f"{attempt.technique} 的 requirement.yaml 无法解析"
            assert isinstance(data.get("interfaces"), list)
            assert all(isinstance(x, str) for x in data["interfaces"]), (
                f"{attempt.technique}: 接口条目必须解析成字符串，而不是映射"
            )
            checked += 1
    assert checked >= 6


def test_multi_case_techniques_expose_distinct_case_ids():
    equivalents = TECHNIQUES_BY_NAME["equivalent_op"].generate_all("需求")
    assert len(equivalents) == len(VARIANTS)
    assert len({a.case_id for a in equivalents}) == len(VARIANTS)
    assert all(a.case_id for a in equivalents)

    sub_tasks = TECHNIQUES_BY_NAME["risk_camouflage"].generate_all("需求")
    assert [a.case_id for a in sub_tasks] == [
        "password_check",
        "session_manager",
        "permission_check",
    ]
    assert len({a.requirement for a in sub_tasks}) == 3


def test_risk_camouflage_shares_one_rollup_ledger():
    attempts = TECHNIQUES_BY_NAME["risk_camouflage"].generate_all("需求")
    ledgers = {
        a.config_overrides["defense"]["human_review"]["rollup_ledger"] for a in attempts
    }
    assert ledgers == {"{sandbox_root}/human_review_ledger.jsonc".replace("jsonc", "jsonl")}
    assert attempts[0].expectation == "partial"


def test_equivalent_op_variants_differ_in_payload():
    attempts = TECHNIQUES_BY_NAME["equivalent_op"].generate_all("需求")
    bodies = {
        call.content
        for attempt in attempts
        for call in attempt.calls
        if call.tool == "write_file" and call.path == "mod.py"
    }
    commands = {
        call.command
        for attempt in attempts
        for call in attempt.calls
        if call.tool == "execute_command"
    }
    assert len(bodies) == 2, "变体应覆盖「危险实现」与「良性实现 + 危险命令」两类写法"
    assert any("shutil.rmtree" in b for b in bodies)
    shell_variants = [v for v in VARIANTS if v.commands]
    # 每个 shell 变体一条独立命令，外加标准流程里的 pytest 命令
    assert len(commands) == len(shell_variants) + 1, "每个 shell 变体都应有独立的命令载荷"
    assert all(v.commands[0] in commands for v in shell_variants)


def test_techniques_declaring_llm_requirement_gate_on_offline_runs():
    for name in ("coverage_disguise", "constraint_weaken", "constraint_replace"):
        assert TECHNIQUES_BY_NAME[name].requires == frozenset({Requirement.LLM})
        # 离线运行时防线 2 只能 fail-closed，因此这些技术必须保留防线 2 参与判定
        attempt = TECHNIQUES_BY_NAME[name].generate("需求")
        assert attempt.enabled_defenses is None


def test_late_defenses_exclude_offline_unavailable_line():
    for name in ("formal_evade", "equivalent_op", "timing_evade", "risk_camouflage"):
        for attempt in TECHNIQUES_BY_NAME[name].generate_all("需求"):
            assert attempt.enabled_defenses == (1, 3, 4, 5)
            assert 2 not in attempt.enabled_defenses


def test_sandbox_config_effective_defenses_can_only_narrow():
    from pathlib import Path

    from benchmarks.red_team.agent import RedTeamSandbox

    offline = RedTeamSandbox(Path("."), SandboxConfig(llm_available=False))
    attempt = TECHNIQUES_BY_NAME["equivalent_op"].generate("需求")
    assert offline.effective_defenses(attempt) == (1, 3, 4, 5)

    # 有模型时一律跑全集，让防线 2 也能真正参与判定
    online = RedTeamSandbox(Path("."), SandboxConfig(llm_available=True))
    assert online.effective_defenses(attempt) == (1, 2, 3, 4, 5)

    # 案例只能收窄，不能扩权
    narrow = RedTeamSandbox(Path("."), SandboxConfig(enabled_defenses=(4,)))
    assert narrow.effective_defenses(attempt) == (4,)
    assert offline.effective_defenses(
        TECHNIQUES_BY_NAME["equivalent_op"].generate("需求")
    ) == (1, 3, 4, 5)
