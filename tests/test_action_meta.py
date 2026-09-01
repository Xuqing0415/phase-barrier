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
    assert action_meta["author"]


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
