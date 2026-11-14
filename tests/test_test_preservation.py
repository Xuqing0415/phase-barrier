"""测试演进约束：真实状态、按文件身份、显式删除、旧状态和 Xarray 原始轨迹。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.languages.go import GoAdapter
from anti_shortcut.languages.java import JavaAdapter
from anti_shortcut.languages.python import PythonAdapter
from anti_shortcut.validators import validate_implementation, validate_tests
from conftest import SPEC


ORIGINAL = "def test_one():\n    assert 1 == 1\n\ndef test_two():\n    assert 2 == 2\n"


def setup_baseline(tmp_path, mode="strict", text=ORIGINAL):
    config = GateConfig(require_implementation=False, min_test_functions=1,
                        stages={"implementation": {"test_preservation": mode}})
    path = tmp_path / "test_repro.py"
    path.write_text(text, encoding="utf-8")
    ok, msg, baseline = validate_tests(tmp_path, config, None)
    assert ok, msg
    state = SimpleNamespace(snapshot=lambda: {"stage_history": [{"stage": 2, "evidence": baseline}]})
    return config, path, state, baseline


def result(tmp_path, config, state, evidence=None):
    return validate_implementation(tmp_path, config, state, evidence=evidence)


def test_default_and_yaml_modes(tmp_path):
    assert GateConfig().stages.implementation.test_preservation == "warn"
    path = tmp_path / "config.yaml"
    path.write_text("stages:\n  implementation:\n    test_preservation: strict\n", encoding="utf-8")
    assert load_config(path).stages.implementation.test_preservation == "strict"
    with pytest.raises(ValidationError):
        GateConfig(stages={"implementation": {"test_preservation": "strcit"}})


@pytest.mark.parametrize("text", [ORIGINAL, ORIGINAL + "\ndef test_three():\n    assert True\n",
                                 "\n\n".join(reversed(ORIGINAL.strip().split("\n\n"))) + "\n"])
def test_preserved_added_and_reordered_names(tmp_path, text):
    config, path, state, baseline = setup_baseline(tmp_path)
    assert baseline["test_functions_by_file"] == {"test_repro.py": ["test_one", "test_two"]}
    path.write_text(text, encoding="utf-8")
    ok, _, ev = result(tmp_path, config, state)
    assert ok and not ev["test_preservation"]["missing"]


@pytest.mark.parametrize("mode,allowed,status", [("strict", False, "rejected"), ("warn", True, "warning")])
def test_deleted_name(tmp_path, mode, allowed, status):
    config, path, state, _ = setup_baseline(tmp_path, mode)
    path.write_text("def test_one():\n    assert True\n", encoding="utf-8")
    ok, msg, ev = result(tmp_path, config, state)
    assert ok is allowed and "test_two" in msg
    assert ev["test_preservation"]["status"] == status
    assert ev["test_preservation"]["undeclared"] == [{"file": "test_repro.py", "name": "test_two"}]


def test_renamed_name_rejected(tmp_path):
    config, path, state, _ = setup_baseline(tmp_path)
    path.write_text(ORIGINAL.replace("test_two", "test_other"), encoding="utf-8")
    assert result(tmp_path, config, state)[0] is False


def test_same_names_changed_assertions_only_warn(tmp_path):
    config, path, state, _ = setup_baseline(tmp_path)
    path.write_text(ORIGINAL.replace("assert 2 == 2", "assert True"), encoding="utf-8")
    ok, _, ev = result(tmp_path, config, state)
    assert ok and ev["test_preservation"]["status"] == "warning"
    assert ev["test_preservation"]["changed_files"] == ["test_repro.py"]


def test_declared_deletion_and_missing_file_allowed(tmp_path):
    config, path, state, _ = setup_baseline(tmp_path)
    path.unlink()
    removals = [{"file": path.name, "name": name, "reason": "Replaced by integration coverage; review needed"}
                for name in ("test_one", "test_two")]
    ok, _, ev = result(tmp_path, config, state, {"test_removals": removals})
    assert ok and ev["test_preservation"]["declared_removals"] == removals
    assert not ev["test_preservation"]["undeclared"]


@pytest.mark.parametrize("declaration", [None, "test_two", {}, [{"name": "test_two", "reason": "x"}],
                                      [{"file": "test_repro.py", "name": "test_two", "reason": "  "}],
                                      [{"file": "wrong.py", "name": "test_two", "reason": "x"}],
                                      [{"file": "test_repro.py", "name": "test_two", "reason": 1}]])
def test_malformed_or_mismatched_declarations_do_not_waive(tmp_path, declaration):
    config, path, state, _ = setup_baseline(tmp_path)
    path.write_text("def test_one():\n    assert True\n", encoding="utf-8")
    assert result(tmp_path, config, state, {"test_removals": declaration})[0] is False


def test_declaration_only_waives_named_test(tmp_path):
    config, path, state, _ = setup_baseline(tmp_path)
    path.unlink()
    ok, _, ev = result(tmp_path, config, state, {"test_removals": [
        {"file": path.name, "name": "test_one", "reason": "removed"}]})
    assert not ok and [t["name"] for t in ev["test_preservation"]["undeclared"]] == ["test_two"]


def test_same_name_in_other_file_cannot_substitute(tmp_path):
    config, path, state, _ = setup_baseline(tmp_path)
    path.write_text("def test_one():\n    assert True\n", encoding="utf-8")
    (tmp_path / "test_other.py").write_text(ORIGINAL, encoding="utf-8")
    assert result(tmp_path, config, state)[0] is False


def test_syntax_error_in_registered_tests_rejected(tmp_path):
    config, path, state, _ = setup_baseline(tmp_path)
    path.write_text("def test_one(:", encoding="utf-8")
    ok, _, ev = result(tmp_path, config, state)
    assert not ok and ev["test_preservation"]["unreadable_files"] == [path.name]


def test_off_does_not_read_tests(tmp_path, monkeypatch):
    config, path, state, _ = setup_baseline(tmp_path, "off")
    path.unlink()
    def unexpected(*args):
        pytest.fail("off must not parse registered tests")
    monkeypatch.setattr(PythonAdapter, "analyze_tests", unexpected)
    assert result(tmp_path, config, state) == (True, "实现代码校验通过（0 个文件，语法检查 OK）", {"files": [], "sha256": {}})


@pytest.mark.parametrize("state", [None, SimpleNamespace(snapshot=lambda: {}),
                                 SimpleNamespace(snapshot=lambda: {"stage_history": [{"stage": 2, "evidence": {
                                     "files": ["test_empty.py"], "test_functions": [],
                                     "test_functions_by_file": {"test_empty.py": []}}}]}),
                                 SimpleNamespace(snapshot=lambda: {"stage_history": [{"stage": 2, "evidence": {}}]})])
def test_no_registered_tests_skips_check(tmp_path, state):
    assert result(tmp_path, GateConfig(require_implementation=False), state)[0] is True


def test_legacy_flat_baseline_is_used_without_rebuilding(tmp_path):
    config, path, state, baseline = setup_baseline(tmp_path)
    baseline.pop("test_functions_by_file")
    path.write_text(ORIGINAL.replace("test_two", "test_replacement"), encoding="utf-8")
    ok, _, ev = result(tmp_path, config, state)
    assert not ok and ev["test_preservation"]["baseline_format"] == "legacy_flat"
    assert ev["test_preservation"]["missing"] == [{"file": None, "name": "test_two"}]


def test_stage2_caller_cannot_replace_baseline(tmp_path):
    skill = AntiShortcutSkill(tmp_path, user_request="fix", config={"min_test_functions": 1,
                               "stages": {"implementation": {"test_preservation": "strict"}}})
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    assert skill.advance_stage(2)["success"]
    (tmp_path / "test_repro.py").write_text(ORIGINAL, encoding="utf-8")
    advanced = skill.advance_stage(3, evidence={"test_functions": [], "test_functions_by_file": {}, "sha256": {}})
    assert advanced["success"] and advanced["evidence"]["test_count"] == 2
    assert advanced["evidence"]["test_functions"] == ["test_one", "test_two"]
    (tmp_path / "impl.py").write_text("answer = 42\n", encoding="utf-8")
    (tmp_path / "test_repro.py").write_text("def test_one():\n    assert True\n", encoding="utf-8")
    rejected = skill.advance_stage(4, evidence={"test_preservation": {"status": "preserved"}})
    assert not rejected["success"] and skill.current_stage == 3
    removal = {"file": "test_repro.py", "name": "test_two", "reason": "Merged duplicate behavior into test_one"}
    advanced = skill.advance_stage(4, evidence={"test_removals": [removal]})
    assert advanced["success"] and skill.current_stage == 4
    events = [json.loads(line) for line in (skill.gate_dir / "audit.log").read_text(encoding="utf-8").splitlines()]
    audited = [e for e in events if e.get("event") == "test_removed_with_reason"]
    assert len(audited) == 1 and all(audited[0][k] == v for k, v in removal.items())


@pytest.mark.parametrize("adapter,filename,text,names", [
    (JavaAdapter(), "ReproTest.java", """class ReproTest {
      // @Test void fake() {}
      @Test public void first() { assertTrue(true); }
      @org.junit.jupiter.api.Test
      void second() { assertEquals(1, 1); }
    }""", ["first", "second"]),
    (GoAdapter(), "repro_test.go", """package repro
func TestFirst(t *testing.T) { t.Fatal("fail") }
func TestSecond(t *testing.T) { t.Fatal("fail") }
""", ["TestFirst", "TestSecond"]),
])
def test_java_go_extract_stable_names_and_detect_rename(tmp_path, adapter, filename, text, names):
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    extracted = [t["name"] for t in adapter.analyze_tests(path)["test_functions"]]
    assert extracted == names
    config = GateConfig(require_implementation=False, min_test_functions=1,
                        stages={"implementation": {"test_preservation": "strict"}})
    ok, msg, baseline = validate_tests(tmp_path, config, None, adapter)
    assert ok, msg
    state = SimpleNamespace(snapshot=lambda: {"stage_history": [{"stage": 2, "evidence": baseline}]})
    path.write_text(text.replace(names[-1], names[-1] + "Renamed"), encoding="utf-8")
    ok, _, ev = validate_implementation(tmp_path, config, state, adapter)
    assert not ok and ev["test_preservation"]["missing"][0]["name"] == names[-1]


def test_real_xarray_stage2_stage3_replacement_rejected(tmp_path):
    fixtures = Path(__file__).parent / "fixtures" / "test_preservation" / "xarray_3993"
    stage2 = (fixtures / "stage2.py.txt").read_text(encoding="utf-8")
    stage3 = (fixtures / "stage3.py.txt").read_text(encoding="utf-8")
    config, path, state, _ = setup_baseline(tmp_path, text=stage2)
    path.write_text(stage3, encoding="utf-8")
    ok, _, ev = result(tmp_path, config, state)
    assert not ok
    assert ev["test_preservation"]["missing"] == [
        {"file": "test_repro.py", "name": "test_integrate_coord_and_dim_raises"}]
