"""红队残余风险白名单与 CI 门禁语义（路径 1，v1.0.1）。

口径：**任何逃逸都算失败**，唯一例外是
``benchmarks/red_team/known_residual.yaml`` 里显式登记、且 ``expires_at`` 未过期的案例。
本文件把这条口径固化成回归测试：

- 白名单解析与校验（缺文件 / 结构错 / 缺字段 / 日期非法都要报错）；
- ``evaluate_escapes`` 的分桶（豁免 / 未豁免 / 已过期豁免）；
- CLI ``--check-residual-expiry`` 的退出码；
- 「逃逸即失败」的下限：去掉白名单后，既有逃逸立刻让 ``--fail-on-vulnerability`` 返回非 0。

这些用例是「为什么这个 bug 能进 main」这一问题的机制答案：逃逸不再由技术自带的
``expectation`` 自证为残余风险，必须走 `known_residual.yaml` 的人工评审与到期复核。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from benchmarks.red_team.residuals import (
    DEFAULT_RESIDUALS_FILE,
    ResidualConfigError,
    ResidualWaiver,
    evaluate_escapes,
    load_residuals,
)
from benchmarks.red_team.run_red_team import main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_whitelist_is_valid_and_unexpired():
    waivers = load_residuals(DEFAULT_RESIDUALS_FILE)
    assert {w.technique for w in waivers} == {
        "session_family_split",
        "session_family_stagger",
        "session_family_below_threshold",
    }
    today = date.today()
    assert all(not w.expired(today) for w in waivers)
    assert all(w.reason.strip() for w in waivers)
    assert all(w.case_id for w in waivers)


def test_load_residuals_rejects_missing_file(tmp_path):
    with pytest.raises(ResidualConfigError):
        load_residuals(tmp_path / "nope.yaml")


@pytest.mark.parametrize(
    "body",
    [
        "version: 1\n",                                     # 缺 entries
        "entries:\n  - technique: a\n",                     # 条目缺字段
        "version: 1\nentries:\n  - technique: a\n    case_id: '*'\n    reason: r\n"
        "    expires_at: not-a-date\n",                     # 日期非法
        "- just\n- a\n- list\n",                            # 顶层不是映射
    ],
)
def test_load_residuals_rejects_bad_structure(tmp_path, body):
    p = tmp_path / "bad.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(ResidualConfigError):
        load_residuals(p)


def test_evaluate_escapes_partitions_waived_unwaived_and_expired():
    today = date(2026, 6, 1)
    waivers = [
        ResidualWaiver("tech_a", "01-*", "固有窗口", date(2027, 1, 1)),
        ResidualWaiver("tech_b", "*", "已过期", date(2026, 1, 1)),
    ]
    rows = [
        {"technique": "tech_a", "case_id": "01-x", "outcome": "escaped"},
        {"technique": "tech_a", "case_id": "99-x", "outcome": "escaped"},
        {"technique": "tech_b", "case_id": "any", "outcome": "escaped"},
        {"technique": "tech_a", "case_id": "01-y", "outcome": "blocked"},
    ]
    gate = evaluate_escapes(rows, waivers, today=today)
    assert [r["case_id"] for r in gate["waived"]] == ["01-x"]
    assert {r["case_id"] for r in gate["unwaived"]} == {"99-x", "any"}
    assert [w.technique for w in gate["expired_waivers"]] == ["tech_b"]


def test_evaluate_escapes_non_escaped_rows_are_ignored():
    rows = [
        {"technique": "t", "case_id": "c", "outcome": "blocked"},
        {"technique": "t", "case_id": "c", "outcome": "inconclusive"},
        {"technique": "t", "case_id": "c", "outcome": "skipped"},
    ]
    gate = evaluate_escapes(rows, [])
    assert gate == {"waived": [], "unwaived": [], "expired_waivers": []}


def test_real_whitelist_covers_documented_residuals_but_not_new_escapes():
    waivers = load_residuals()
    documented = [
        {"technique": "session_family_split", "case_id": "01-a", "outcome": "escaped"},
        {"technique": "session_family_split", "case_id": "02-a", "outcome": "escaped"},
        {"technique": "session_family_stagger", "case_id": "01-a", "outcome": "escaped"},
        {"technique": "session_family_stagger", "case_id": "02-a", "outcome": "escaped"},
        {"technique": "session_family_below_threshold", "case_id": "03-a", "outcome": "escaped"},
    ]
    gate = evaluate_escapes(documented, waivers)
    assert gate["unwaived"] == [] and len(gate["waived"]) == 5

    # 白名单是「逐条登记」，不是「全局免罪」：新技术的逃逸必须落进 unwaived
    fresh = [{"technique": "compact_redirection", "case_id": "compact_gt", "outcome": "escaped"}]
    assert [r["case_id"] for r in evaluate_escapes(fresh, waivers)["unwaived"]] == ["compact_gt"]


def test_check_residual_expiry_cli_exit_codes(tmp_path):
    valid = tmp_path / "valid.yaml"
    valid.write_text(
        "version: 1\nentries:\n  - technique: t\n    case_id: '*'\n"
        "    reason: r\n    expires_at: 2999-01-01\n",
        encoding="utf-8",
    )
    assert main(["--check-residual-expiry", "--known-residual", str(valid)]) == 0

    expired = tmp_path / "expired.yaml"
    expired.write_text(
        "version: 1\nentries:\n  - technique: t\n    case_id: '*'\n"
        "    reason: r\n    expires_at: 2000-01-01\n",
        encoding="utf-8",
    )
    assert main(["--check-residual-expiry", "--known-residual", str(expired)]) == 1

    broken = tmp_path / "broken.yaml"
    broken.write_text("version: 1\n", encoding="utf-8")
    assert main(["--check-residual-expiry", "--known-residual", str(broken)]) == 2


def test_escape_fails_cli_when_whitelist_removed(tmp_path):
    """「逃逸即失败」的下限：既有残余风险一旦去掉豁免，CI 立刻变红。"""
    code = main(
        [
            "--techniques",
            "session_family_below_threshold",
            "--workspace",
            str(tmp_path / "ws"),
            "--fail-on-vulnerability",
            "--no-known-residual",
        ]
    )
    assert code == 1


def test_red_team_workflow_fails_on_escape_and_checks_expiry():
    text = (REPO_ROOT / ".github" / "workflows" / "red-team.yml").read_text(encoding="utf-8")
    assert "--fail-on-vulnerability" in text
    assert "--check-residual-expiry" in text
    assert "compact_redirection" in text  # 快速子集必须覆盖紧凑语法这类逃逸