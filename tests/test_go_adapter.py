"""Go 语言适配器测试：文件识别 / gofmt 语法检查 / go test 输出解析 / Skill 全流程。"""
import shutil
import subprocess
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.languages import LANGUAGE_REGISTRY, GoAdapter, detect_language, get_adapter
from anti_shortcut.validators import validate_tests
from conftest import SPEC, USER_REQUEST

GO_IMPL = """\
package fib

func fib(n int) int {
    if n <= 1 {
        return n
    }
    a, b := 0, 1
    for i := 2; i <= n; i++ {
        a, b = b, a+b
    }
    return b
}
"""

GO_TESTS = """\
package fib

import "testing"

func TestFibBasic(t *testing.T) {
    if got := fib(3); got != 2 {
        t.Errorf("fib(3) = %d, want 2", got)
    }
}

func TestFibNegative(t *testing.T) {
    if got := fib(-1); got != 0 {
        t.Fatalf("fib(-1) = %d, want 0", got)
    }
}
"""

needs_gofmt = pytest.mark.skipif(
    GoAdapter._find_gofmt() is None, reason="Go 工具链未安装"
)


# ---------- 注册与检测 ----------

def test_go_adapter_registered():
    assert "go" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["go"] is GoAdapter


def test_go_adapter_detected_via_go_mod(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/fib\n", encoding="utf-8")
    assert detect_language(tmp_path) == "go"
    cfg = GateConfig()
    assert isinstance(get_adapter(cfg, tmp_path), GoAdapter)


# ---------- 文件识别 ----------

def test_go_adapter_file_classification():
    a = GoAdapter()
    assert a.is_test_file(Path("fib_test.go"))
    assert a.is_test_file(Path("internal/fib/fib_test.go"))
    assert not a.is_test_file(Path("fib.go"))
    assert a.is_source_file(Path("fib.go"))
    assert a.is_source_file(Path("cmd/app/main.go"))
    assert not a.is_source_file(Path("fib_test.go"))
    assert not a.is_source_file(Path("go.mod"))
    assert not a.is_source_file(Path("README.md"))


# ---------- 测试统计（启发式） ----------

def test_go_adapter_analyze_tests(tmp_path):
    f = tmp_path / "fib_test.go"
    f.write_text(GO_TESTS, encoding="utf-8")
    info = GoAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 2


def test_go_adapter_analyze_tests_empty(tmp_path):
    f = tmp_path / "empty_test.go"
    f.write_text("package fib\n", encoding="utf-8")
    info = GoAdapter().analyze_tests(f)
    assert info["test_functions"] == []
    assert info["assertions_total"] == 0


# ---------- 语法检查 ----------

def test_go_adapter_check_syntax_empty_file(tmp_path):
    f = tmp_path / "Empty.go"
    f.write_text("", encoding="utf-8")
    ok, msg = GoAdapter().check_syntax(f)
    assert not ok and "空文件" in msg


def test_go_adapter_check_syntax_missing_gofmt(tmp_path, monkeypatch):
    f = tmp_path / "fib.go"
    f.write_text(GO_IMPL, encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.go.shutil.which", lambda name: None)
    ok, msg = GoAdapter().check_syntax(f)
    assert not ok and "Go" in msg


@needs_gofmt
def test_go_adapter_check_syntax_ok(tmp_path):
    f = tmp_path / "fib.go"
    f.write_text(GO_IMPL, encoding="utf-8")
    ok, msg = GoAdapter().check_syntax(f)
    assert ok and "gofmt" in msg


@needs_gofmt
def test_go_adapter_check_syntax_error(tmp_path):
    f = tmp_path / "Broken.go"
    f.write_text("package fib\n\nfunc fib(n int) int {\n", encoding="utf-8")
    ok, msg = GoAdapter().check_syntax(f)
    assert not ok and "Go 语法错误" in msg


# ---------- 测试命令识别 ----------

def test_go_adapter_identify_test_command():
    a = GoAdapter()
    assert a.identify_test_command("go test ./...")
    assert a.identify_test_command("go test -v ./internal/...")
    assert a.identify_test_command("go vet ./...")
    assert not a.identify_test_command("go build ./...")
    assert not a.identify_test_command("go run main.go")
    assert not a.identify_test_command("ls -la")


# ---------- 测试输出解析 ----------

def test_go_adapter_parse_test_output():
    a = GoAdapter()
    ok, summary = a.parse_test_output("ok  \texample.com/fib\t0.123s\n", 0)
    assert ok and "ok" in summary
    ok2, summary2 = a.parse_test_output(
        "--- FAIL: TestFibBasic (0.00s)\n    fib_test.go:10: fib(3) = 1, want 2\nFAIL\nFAIL\texample.com/fib\t0.123s\n",
        1,
    )
    assert not ok2 and "FAIL" in summary2
    ok3, summary3 = a.parse_test_output("FAIL", 1)
    assert not ok3
    ok4, _ = a.parse_test_output("no test files", 1)
    assert not ok4
    ok5, _ = a.parse_test_output("whatever", 0)
    assert ok5


# ---------- 校验器接线 ----------

def test_validate_tests_go_with_language_config(tmp_path):
    (tmp_path / "fib_test.go").write_text(GO_TESTS, encoding="utf-8")
    cfg = load_config({"language": "go"})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok
    assert ev["test_count"] == 2


# ---------- Skill 全流程验收 ----------

def test_skill_go_full_flow(tmp_path, monkeypatch, fake_tools):
    """验收：language: go 时阶段校验与工具拦截生效，可完整走通交付。"""
    monkeypatch.setattr(
        "anti_shortcut.languages.go.shutil.which",
        lambda name: "gofmt" if name == "gofmt" else None,
    )

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.go.subprocess.run", fake_run)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")

    skill = AntiShortcutSkill(tmp_path, config={"language": "go"}, user_request=USER_REQUEST)
    tools = skill.install(fake_tools)
    assert isinstance(skill.adapter, GoAdapter)

    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("fib_test.go", GO_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("fib.go", GO_IMPL)
    assert tools["advance_stage"](4)["success"]
    skill.state.mark_test_run({"exit_code": 0, "passed": True, "summary": "ok example.com/fib"})
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 6
    assert skill.is_complete


# ---------- v0.10.0：输出解析增强 ----------

def test_go_adapter_parse_failure_names():
    a = GoAdapter()
    out = (
        "=== RUN   TestFibBasic\n"
        "--- FAIL: TestFibBasic (0.00s)\n"
        "    fib_test.go:10: fib(3) = 1, want 2\n"
        "=== RUN   TestFibNegative\n"
        "--- FAIL: TestFibNegative (0.00s)\n"
        "    fib_test.go:20: fib(-1) = 99, want 0\n"
        "FAIL\nFAIL\texample.com/fib\t0.123s\n"
    )
    ok, summary = a.parse_test_output(out, 1)
    assert not ok
    assert "TestFibBasic" in summary and "TestFibNegative" in summary
    assert "2 个" in summary


def test_go_adapter_parse_verbose_pass_count():
    a = GoAdapter()
    out = (
        "=== RUN   TestFibBasic\n"
        "--- PASS: TestFibBasic (0.00s)\n"
        "=== RUN   TestFibNegative\n"
        "--- PASS: TestFibNegative (0.00s)\n"
        "PASS\n"
    )
    ok, summary = a.parse_test_output(out, 0)
    assert ok and "2 个用例通过" in summary


def test_summarize_test_output_prefers_go_adapter():
    from anti_shortcut.interceptors import summarize_test_output

    rec = summarize_test_output("ok  \texample.com/fib\t0.123s\n", 0, adapter=GoAdapter())
    assert rec["passed"] is True
    assert "example.com/fib" in rec["summary"]
