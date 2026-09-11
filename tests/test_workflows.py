"""CI 工作流静态校验（v0.60.0）：把本轮修掉的几类「静默绿」CI bug 固化成回归测试。

背景（都是真实发生过的缺陷）：
1. learning-loop 用 ``hashFiles()`` 判断 workspace 之外的 ``$RUNNER_TEMP`` 文件；
   拿不到就返回空串，后续步骤被静默跳过，job 依旧全绿；
2. 复测步骤漏了 ``--fail-on-vulnerability``：``run_red_team.py`` 无论是否发现真实
   漏洞都返回 0，``clean`` 恒为 true，「复测不过 -> 建 issue」这条安全路径成了死代码；
3. ``update_rules.py`` 的 ``--promote`` 模式被 ``--suggestions`` 必填与
   ``--verify-cases`` 前置校验挡下（退出码 2），CI 里的 promote 步骤必失败。

这里只做静态检查（不联网、不改文件），保证同类回归会在 pytest 阶段被抓住。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))
STEP_REF = re.compile(r"steps\.([A-Za-z_][A-Za-z0-9_-]*)\.outputs")


class _StrictLoader(yaml.SafeLoader):
    """拒绝重复键：PyYAML 默认静默「后者覆盖前者」，在 CI 配置里是真实事故来源。"""


def _construct_mapping(loader: yaml.SafeLoader, node, deep: bool = False):
    loader.flatten_mapping(node)
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping
)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)


def _jobs(path: Path):
    for job_name, job in (_load(path).get("jobs") or {}).items():
        yield job_name, job


def _steps(path: Path):
    for job_name, job in _jobs(path):
        for step in job.get("steps") or []:
            yield job_name, step


def test_workflow_files_are_discovered():
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "red-team.yml", "learning-loop.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflows_parse_without_duplicate_keys(path: Path):
    data = _load(path)
    assert isinstance(data, dict)
    assert data.get("jobs"), f"{path.name} 没有 jobs"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_step_has_run_or_uses(path: Path):
    for job_name, step in _steps(path):
        keys = set(step)
        assert ("run" in keys) ^ ("uses" in keys), f"{path.name}:{job_name} -> {step!r}"
        if "run" in keys:
            assert isinstance(step["run"], str) and step["run"].strip()


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_step_output_references_resolve(path: Path):
    for job_name, job in _jobs(path):
        ids = {step["id"] for step in job.get("steps") or [] if "id" in step}
        for step in job.get("steps") or []:
            blob = json.dumps(step, ensure_ascii=False)
            for ref in STEP_REF.findall(blob):
                assert ref in ids, f"{path.name}:{job_name} 引用了不存在的 step id {ref!r}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_needs_references_resolve(path: Path):
    jobs = _load(path).get("jobs") or {}
    for job_name, job in jobs.items():
        needs = job.get("needs") or []
        if isinstance(needs, str):
            needs = [needs]
        for dep in needs:
            assert dep in jobs, f"{path.name}:{job_name} needs 了不存在的 job {dep!r}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_hashfiles_gating_on_runner_temp(path: Path):
    """hashFiles() 只可靠地覆盖 workspace 内的文件，别拿它判断 $RUNNER_TEMP。"""
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"hashFiles\(", text):
        window = text[match.end() : match.end() + 200]
        assert "runner.temp" not in window, (
            f"{path.name}: hashFiles() 不能用来判断 workspace 之外的 $RUNNER_TEMP"
            "（拿不到就返回空串，会让步骤静默跳过、job 依旧全绿）"
        )


def test_learning_loop_retest_fails_on_vulnerability():
    text = (WORKFLOW_DIR / "learning-loop.yml").read_text(encoding="utf-8")
    retest = [c for c in text.split("- name:") if c.lstrip().startswith("Re-run red team")]
    assert len(retest) == 1, "learning-loop 的复测步骤不见了"
    assert "run_red_team.py" in retest[0]
    assert "--fail-on-vulnerability" in retest[0], (
        "复测漏了 --fail-on-vulnerability：run_red_team.py 恒返回 0，"
        "clean 恒为 true，「复测不过 -> 建 issue」会变成死代码"
    )


def test_learning_loop_gates_on_step_output_not_hashfiles():
    text = (WORKFLOW_DIR / "learning-loop.yml").read_text(encoding="utf-8")
    assert "steps.collect.outputs.has_cases == 'true'" in text
    assert "id: collect" in text


def test_learning_loop_falls_back_to_issue_when_pr_creation_is_denied():
    """仓库未开「Allow GitHub Actions to create and approve pull requests」时，
    ``gh pr create`` 必被拒（GraphQL: not permitted to create pull requests）。
    workflow 必须降级成「issue + 一键建 PR 链接」，而不是让整个 job 变红。
    """
    text = (WORKFLOW_DIR / "learning-loop.yml").read_text(encoding="utf-8")
    assert "if pr_error=" in text, "建 PR 失败没有被捕获"
    assert "compare/${base}...${branch}?expand=1" in text, "降级 issue 里缺少一键建 PR 链接"
    assert "gh issue create" in text
    assert "--fail-on-vulnerability" in text  # 复测判定不能被降级逻辑顺手删掉


def test_update_rules_promote_mode_works_without_suggestions():
    """CI 的 promote 步骤必须能在没有 --suggestions / --verify-cases 时跑通。"""
    result = subprocess.run(
        [
            sys.executable,
            "scripts/update_rules.py",
            "--dest",
            "anti_shortcut/defense/learned_rules.yaml",
            "--promote",
            "--confirmations-required",
            "3",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "promoted" in payload


def test_update_rules_rejects_missing_suggestions_outside_promote_mode():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/update_rules.py",
            "--dest",
            "anti_shortcut/defense/learned_rules.yaml",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 2, result.stdout + result.stderr
