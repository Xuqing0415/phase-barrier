import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "swe_agent_audit", Path(__file__).resolve().parents[1] / "benchmarks/swebench/run_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


@pytest.mark.parametrize("raise_permission", [True, False])
def test_run_counts_each_denial_once_and_exports_trace(monkeypatch, tmp_path, raise_permission):
    import anti_shortcut

    class Gate:
        current_stage = 1
        is_complete = False

        def __init__(self, *args, **kwargs):
            self.state = SimpleNamespace(delivery_clean=lambda: False,
                                         snapshot=lambda: {"current_stage": self.current_stage})

        def install(self, tools):
            return tools

        def advance_stage(self, target):
            if target == 2:
                self.current_stage = 2
                return {"success": True}
            return {"success": False}

    def dispatch(self, name, args):
        if name == "write_file":
            if raise_permission:
                raise PermissionError("blocked")
            return {"gate_denied": "blocked"}
        return {"exit_code": 0}

    calls = [("write_file", {"path": "x.py", "content": "test-secret"}),
             ("advance_stage", {"new_stage": 6}),
             ("advance_stage", {"new_stage": 2}),
             ("execute_command", {"command": "echo ok"})]
    response = {"choices": [{"message": {"tool_calls": [
        {"id": str(i), "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(calls)]}}]}
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    monkeypatch.setattr(anti_shortcut, "AntiShortcutSkill", Gate)
    monkeypatch.setattr(agent.AgentTools, "dispatch", dispatch)
    monkeypatch.setattr(agent, "call_model", lambda *args: response)
    monkeypatch.setattr(agent, "load_instance", lambda *args: {"problem_statement": "test"})
    monkeypatch.setattr(agent, "extract_patch", lambda *args: ("patch", ""))
    monkeypatch.setattr(agent.subprocess, "run", lambda *args, **kwargs: None)
    args = SimpleNamespace(workdir=tmp_path, outdir=tmp_path / "runs", label="audit",
                           dataset=tmp_path / "unused", instance="test", mode="gated",
                           venv=None, exec_timeout=1, model="test", max_turns=1)
    assert agent.run(args) == 0
    output = args.outdir / args.label
    stats = json.loads((output / "stats.json").read_text())
    text = (output / "trace.jsonl").read_text()
    events = [json.loads(line) for line in text.splitlines()]
    assert stats["gate_intercepts"] == sum(e["blocked"] for e in events) == 2
    assert [e["event"] for e in events] == ["blocked", "blocked", "tool_call", "tool_call"]
    assert events[2]["stage_before"] == 1 and events[2]["stage_after"] == 2
    assert "test-secret" not in text
    assert json.loads((output / "state.json").read_text())["current_stage"] == 2


def test_audit_write_failure_is_not_silently_ignored(tmp_path):
    (tmp_path / "trace.jsonl").mkdir()
    with pytest.raises(OSError):
        agent.write_audit_event(tmp_path, {"blocked": True}, "")
