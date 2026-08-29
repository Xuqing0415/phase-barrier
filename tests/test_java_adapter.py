"""Java 语言适配器测试：文件识别 / javac 语法检查 / JUnit 启发式统计 / 输出解析。"""
import shutil
import subprocess
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.languages import LANGUAGE_REGISTRY, JavaAdapter, detect_language, get_adapter
from anti_shortcut.validators import validate_tests
from conftest import SPEC, USER_REQUEST

JAVA_IMPL = """\
class Calc {
    static int add(int a, int b) {
        return a + b;
    }
}
"""

JAVA_TESTS = """\
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertEquals;

class CalcTest {
    @Test
    void addBasic() {
        assertEquals(3, Calc.add(1, 2));
    }

    @Test
    void addNegative() {
        assertEquals(0, Calc.add(-1, 1));
    }
}
"""

needs_javac = pytest.mark.skipif(shutil.which("javac") is None, reason="JDK 未安装")


# ---------- 注册与检测 ----------

def test_java_adapter_registered():
    assert "java" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["java"] is JavaAdapter


def test_java_adapter_detected_via_pom(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert detect_language(tmp_path) == "java"
    cfg = GateConfig()
    assert isinstance(get_adapter(cfg, tmp_path), JavaAdapter)


# ---------- 文件识别 ----------

def test_java_adapter_file_classification():
    a = JavaAdapter()
    assert a.is_test_file(Path("CalcTest.java"))
    assert a.is_test_file(Path("CalcTests.java"))
    assert a.is_test_file(Path("src/test/java/com/x/Helper.java"))
    assert not a.is_test_file(Path("src/main/java/com/x/Calc.java"))
    assert not a.is_test_file(Path("Calc.java"))
    assert a.is_source_file(Path("src/main/java/com/x/Calc.java"))
    assert a.is_source_file(Path("Calc.java"))
    assert not a.is_source_file(Path("CalcTest.java"))  # 测试文件不算实现
    assert not a.is_source_file(Path("Foo.txt"))


# ---------- 测试统计（启发式） ----------

def test_java_adapter_analyze_tests(tmp_path):
    f = tmp_path / "CalcTest.java"
    f.write_text(JAVA_TESTS, encoding="utf-8")
    info = JavaAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 2


def test_java_adapter_analyze_tests_empty(tmp_path):
    f = tmp_path / "EmptyTest.java"
    f.write_text("class EmptyTest {}\n", encoding="utf-8")
    info = JavaAdapter().analyze_tests(f)
    assert info["test_functions"] == []
    assert info["assertions_total"] == 0


# ---------- 语法检查 ----------

def test_java_adapter_check_syntax_empty_file(tmp_path):
    f = tmp_path / "Empty.java"
    f.write_text("", encoding="utf-8")
    ok, msg = JavaAdapter().check_syntax(f)
    assert not ok and "空文件" in msg


def test_java_adapter_check_syntax_missing_javac(tmp_path, monkeypatch):
    f = tmp_path / "Calc.java"
    f.write_text("class Calc {}\n", encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.java.shutil.which", lambda name: None)
    ok, msg = JavaAdapter().check_syntax(f)
    assert not ok and "JDK" in msg


@needs_javac
def test_java_adapter_check_syntax_ok(tmp_path):
    f = tmp_path / "Calc.java"
    f.write_text(JAVA_IMPL, encoding="utf-8")
    ok, msg = JavaAdapter().check_syntax(f)
    assert ok and "javac" in msg


@needs_javac
def test_java_adapter_check_syntax_error(tmp_path):
    f = tmp_path / "Broken.java"
    f.write_text("class Broken { int x = ; }\n", encoding="utf-8")
    ok, msg = JavaAdapter().check_syntax(f)
    assert not ok and "Java 语法错误" in msg


@needs_javac
def test_java_adapter_check_syntax_dependency_tolerated(tmp_path):
    """单文件检查无法解析跨文件依赖：cannot find symbol 应降级为通过。"""
    f = tmp_path / "UsesWidget.java"
    f.write_text(
        "import com.nonexistent.Widget;\nclass UsesWidget { Widget w; }\n",
        encoding="utf-8",
    )
    ok, msg = JavaAdapter().check_syntax(f)
    assert ok and "依赖" in msg


# ---------- 测试命令识别 ----------

def test_java_adapter_identify_test_command():
    a = JavaAdapter()
    assert a.identify_test_command("mvn test")
    assert a.identify_test_command("./mvnw test")
    assert a.identify_test_command("gradle test")
    assert a.identify_test_command("./gradlew test --tests CalcTest")
    assert a.identify_test_command("java -jar junit-platform-console-standalone.jar --scan-class-path")
    assert not a.identify_test_command("mvn package")
    assert not a.identify_test_command("ls -la")


# ---------- 测试输出解析 ----------

def test_java_adapter_parse_test_output():
    a = JavaAdapter()
    ok, summary = a.parse_test_output("Tests run: 3, Failures: 0, Errors: 0\nBUILD SUCCESS", 0)
    assert ok and "Tests run: 3" in summary
    ok2, summary2 = a.parse_test_output("Tests run: 3, Failures: 1, Errors: 0", 1)
    assert not ok2 and "Failures: 1" in summary2
    ok3, summary3 = a.parse_test_output("BUILD FAILURE", 1)
    assert not ok3 and "BUILD" in summary3
    ok4, _ = a.parse_test_output("whatever", 0)
    assert ok4


# ---------- 校验器接线 ----------

def test_validate_tests_java_with_language_config(tmp_path):
    p = tmp_path / "src" / "test" / "java"
    p.mkdir(parents=True)
    (p / "CalcTest.java").write_text(JAVA_TESTS, encoding="utf-8")
    cfg = load_config({"language": "java"})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok
    assert ev["test_count"] == 2


# ---------- Skill 全流程验收 ----------

def test_skill_java_full_flow(tmp_path, monkeypatch, fake_tools):
    """验收：language: java 时阶段校验与工具拦截生效，可完整走通交付。"""
    monkeypatch.setattr(
        "anti_shortcut.languages.java.shutil.which",
        lambda name: "javac" if name == "javac" else None,
    )

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.java.subprocess.run", fake_run)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")

    skill = AntiShortcutSkill(tmp_path, config={"language": "java"}, user_request=USER_REQUEST)
    tools = skill.install(fake_tools)
    assert isinstance(skill.adapter, JavaAdapter)

    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("src/test/java/CalcTest.java", JAVA_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("src/main/java/Calc.java", JAVA_IMPL)
    assert tools["advance_stage"](4)["success"]
    skill.state.mark_test_run({"exit_code": 0, "passed": True, "summary": "Tests run: 2, Failures: 0"})
    r = tools["advance_stage"](5)
    assert r["success"] and r["stage"] == 6
    assert skill.is_complete