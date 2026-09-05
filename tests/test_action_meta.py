"""action.yml 元数据测试（v0.25.0）。

校验 name / description / branding / outputs / inputs 默认值，以及 gate 步骤的
id 与 GITHUB_OUTPUT 写入逻辑。
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def action_meta():
    path = ROOT / "action.yml"
    assert path.exists(), "action.yml 不存在"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_action_name(action_meta):
    assert action_meta["name"] == "Phase-Barrier Gate"


def test_action_description_nonempty(action_meta):
    assert action_meta["description"].strip()


def test_action_author(action_meta):
    assert action_meta["author"] == "Xuqing0415"


def test_action_description_marketplace_ready(action_meta):
    """Marketplace 展示描述：长度 50-200 字符且含核心关键词（v0.44.0）。"""
    desc = action_meta["description"].strip()
    assert 50 <= len(desc) <= 200, f"description 长度 {len(desc)} 超出 50-200"
    lowered = desc.lower()
    for kw in ("stage gate", "coding agent", "SOP", "CI"):
        assert kw.lower() in lowered, f"description 缺少关键词: {kw}"


def test_action_all_inputs_have_descriptions(action_meta):
    """每个 input 都有清晰用途说明，方便 Marketplace 参数引用与工作流编辑器提示。"""
    inputs = action_meta["inputs"]
    assert len(inputs) == 12
    for name, meta in inputs.items():
        desc = (meta.get("description") or "").strip()
        assert len(desc) >= 20, f"inputs.{name} 描述过短"
        assert meta.get("required") is False


def test_action_branding(action_meta):
    branding = action_meta["branding"]
    assert branding["icon"] == "shield"
    assert branding["color"] == "blue"


def test_action_outputs_value_mapping(action_meta):
    """composite action 的 outputs 必须用 value 映射 steps.<id>.outputs.*，
    仅写 $GITHUB_OUTPUT 不会传播到调用方（GitHub Actions 元数据语法）。"""
    outputs = action_meta["outputs"]
    for name in ("workspace", "stage", "allowed"):
        assert outputs[name]["value"] == "${{ steps.gate.outputs.%s }}" % name


def test_action_outputs_declared(action_meta):
    outputs = action_meta.get("outputs")
    assert outputs is not None
    assert set(outputs) == {"workspace", "stage", "allowed"}
    for name, meta in outputs.items():
        assert meta["description"].strip(), f"outputs.{name} 缺少描述"


def test_action_inputs_defaults(action_meta):
    inputs = action_meta["inputs"]
    assert inputs["workspace"]["default"] == "."
    assert inputs["mode"]["default"] == "inspect"
    assert inputs["expected_stage"]["default"] == "6"
    assert inputs["local"]["default"] == "false"
    assert inputs["git_base"]["default"] == "${{ github.event.pull_request.base.sha }}"
    empty_defaults = (
        "to",
        "stage",
        "command",
        "cwd",
        "user_request",
        "version",
        "config",
    )
    for name in empty_defaults:
        assert inputs[name]["default"] == ""


def test_action_verify_mode_wired(action_meta):
    """v0.26.0：verify 模式（PR 增量证据校验）+ git_base 输入接线。"""
    run = next(
        s["run"]
        for s in action_meta["runs"]["steps"]
        if s.get("name") == "Run phase-barrier gate"
    )
    assert '"$MODE" == "verify"' in run
    assert 'verify-evidence --workspace "$WS" --git-base "$GB"' in run
    assert "git_base" in action_meta["inputs"]


def test_action_composite_runs(action_meta):
    assert action_meta["runs"]["using"] == "composite"
    names = [s.get("name") for s in action_meta["runs"]["steps"]]
    assert "Run phase-barrier gate" in names


def test_gate_step_has_id(action_meta):
    steps = action_meta["runs"]["steps"]
    gate = next(s for s in steps if s.get("name") == "Run phase-barrier gate")
    assert gate.get("id") == "gate"


def test_gate_step_writes_default_outputs(action_meta):
    steps = action_meta["runs"]["steps"]
    gate = next(s for s in steps if s.get("name") == "Run phase-barrier gate")
    run = gate["run"]
    assert 'echo "workspace=$WS" >> "$GITHUB_OUTPUT"' in run
    assert 'echo "allowed=false" >> "$GITHUB_OUTPUT"' in run
    assert 'echo "allowed=true" >> "$GITHUB_OUTPUT"' in run


def test_gate_step_writes_stage_on_success(action_meta):
    steps = action_meta["runs"]["steps"]
    gate = next(s for s in steps if s.get("name") == "Run phase-barrier gate")
    run = gate["run"]
    check_echo = 'echo "stage=${{ inputs.stage }}" >> "$GITHUB_OUTPUT"'
    advance_echo = 'echo "stage=${{ inputs.to }}" >> "$GITHUB_OUTPUT"'
    inspect_echo = 'echo "stage=$STAGE" >> "$GITHUB_OUTPUT"'
    assert check_echo in run      # check 模式
    assert advance_echo in run    # advance 模式
    assert inspect_echo in run    # inspect 模式
