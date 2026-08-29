"""Java 语言适配器测试：文件识别 / javac 语法检查 / JUnit 启发式统计 / 输出解析。"""
import os
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
# ---------- 项目级编译（mvn test-compile / gradle compileTestJava） ----------

POM_XML = """<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>fib</artifactId>
  <version>0.1.0</version>
  <properties>
    <maven.compiler.source>11</maven.compiler.source>
    <maven.compiler.target>11</maven.compiler.target>
  </properties>
</project>
"""


def _make_maven_project(tmp_path):
    (tmp_path / "pom.xml").write_text(POM_XML, encoding="utf-8")
    src = tmp_path / "src" / "main" / "java"
    src.mkdir(parents=True)
    (src / "Calc.java").write_text(JAVA_IMPL, encoding="utf-8")
    tst = tmp_path / "src" / "test" / "java"
    tst.mkdir(parents=True)
    (tst / "CalcTest.java").write_text(JAVA_TESTS, encoding="utf-8")
    return src / "Calc.java"


def test_java_adapter_project_compile_maven_ok(tmp_path, monkeypatch):
    f = _make_maven_project(tmp_path)
    monkeypatch.setattr(
        "anti_shortcut.languages.java.shutil.which",
        lambda name: {"mvn": "mvn", "javac": "javac"}.get(name),
    )
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.java.subprocess.run", fake_run)
    ok, msg = JavaAdapter().check_syntax(f)
    assert ok and "test-compile" in msg
    assert "mvn" in seen["cmd"][0]
    assert "-q" in seen["cmd"] and "test-compile" in seen["cmd"]


def test_java_adapter_project_compile_maven_error(tmp_path, monkeypatch):
    f = _make_maven_project(tmp_path)
    monkeypatch.setattr(
        "anti_shortcut.languages.java.shutil.which",
        lambda name: {"mvn": "mvn"}.get(name),
    )
    monkeypatch.setattr(
        "anti_shortcut.languages.java.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="[ERROR] /x/Calc.java:[5,10] cannot find symbol\n[ERROR] -> [Help 1]\n"
        ),
    )
    ok, msg = JavaAdapter().check_syntax(f)
    assert not ok and "项目编译错误" in msg


def test_java_adapter_project_compile_cached(tmp_path, monkeypatch):
    f = _make_maven_project(tmp_path)
    f2 = tmp_path / "src" / "main" / "java" / "Calc2.java"
    f2.write_text("class Calc2 {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "anti_shortcut.languages.java.shutil.which",
        lambda name: {"mvn": "mvn"}.get(name),
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.java.subprocess.run", fake_run)
    adapter = JavaAdapter()
    assert adapter.check_syntax(f)[0]
    assert adapter.check_syntax(f2)[0]
    assert len(calls) == 1  # 同一次指纹命中缓存


def test_java_adapter_project_compile_cache_invalidated(tmp_path, monkeypatch):
    f = _make_maven_project(tmp_path)
    monkeypatch.setattr(
        "anti_shortcut.languages.java.shutil.which",
        lambda name: {"mvn": "mvn"}.get(name),
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.java.subprocess.run", fake_run)
    adapter = JavaAdapter()
    assert adapter.check_syntax(f)[0]
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert adapter.check_syntax(f)[0]
    assert len(calls) == 2  # 文件变化后缓存失效


def test_java_adapter_project_compile_gradle(tmp_path, monkeypatch):
    (tmp_path / "build.gradle").write_text("apply plugin: 'java'\n", encoding="utf-8")
    src = tmp_path / "src" / "main" / "java"
    src.mkdir(parents=True)
    f = src / "Calc.java"
    f.write_text(JAVA_IMPL, encoding="utf-8")
    monkeypatch.setattr(
        "anti_shortcut.languages.java.shutil.which",
        lambda name: {"gradle": "gradle"}.get(name),
    )
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.java.subprocess.run", fake_run)
    ok, msg = JavaAdapter().check_syntax(f)
    assert ok and "gradle" in msg
    assert "compileTestJava" in seen["cmd"]


def test_java_adapter_project_compile_mvnw_preferred(tmp_path, monkeypatch):
    (tmp_path / "pom.xml").write_text(POM_XML, encoding="utf-8")
    mvnw = tmp_path / ("mvnw.cmd" if os.name == "nt" else "mvnw")
    mvnw.write_text("", encoding="utf-8")
    if os.name != "nt":
        mvnw.chmod(0o755)
    src = tmp_path / "src" / "main" / "java"
    src.mkdir(parents=True)
    f = src / "Calc.java"
    f.write_text(JAVA_IMPL, encoding="utf-8")
    monkeypatch.setattr(
        "anti_shortcut.languages.java.shutil.which",
        lambda name: {"mvn": "mvn"}.get(name),
    )
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("anti_shortcut.languages.java.subprocess.run", fake_run)
    ok, msg = JavaAdapter().check_syntax(f)
    assert ok
    assert str(mvnw) in seen["cmd"][0]


# ---------- 输出解析增强（v0.8.0：Surefire Skipped / Gradle / JUnit Console） ----------

def test_java_adapter_parse_test_output_skipped():
    a = JavaAdapter()
    ok, summary = a.parse_test_output(
        "Tests run: 3, Failures: 0, Errors: 0, Skipped: 1\nBUILD SUCCESS", 0
    )
    assert ok and "Skipped" in summary


def test_java_adapter_parse_gradle_output():
    a = JavaAdapter()
    out = (
        "CalcTest > addBasic() PASSED\n"
        "CalcTest > addNegative() FAILED\n"
        "3 tests completed, 1 failed\n"
    )
    ok, summary = a.parse_test_output(out, 1)
    assert not ok and "1 failed" in summary
    ok2, _ = a.parse_test_output("5 tests completed, 0 failed\nBUILD SUCCESSFUL", 0)
    assert ok2


def test_java_adapter_parse_junit_console():
    a = JavaAdapter()
    out = (
        "[         3 containers found      ]\n"
        "[         3 tests found           ]\n"
        "[         3 tests successful      ]\n"
    )
    ok, summary = a.parse_test_output(out, 0)
    assert ok and "successful" in summary
    out2 = (
        "[         3 tests found           ]\n"
        "[         1 tests failed          ]\n"
    )
    ok2, summary2 = a.parse_test_output(out2, 1)
    assert not ok2 and "failed" in summary2


def test_java_adapter_parse_maven_failure_final_summary():
    """失败时取最后一次出现的 Tests run 行（Results: 后的最终汇总）。"""
    a = JavaAdapter()
    out = (
        "Running GoodTest\n"
        "Tests run: 2, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.05 s\n"
        "Running BadTest\n"
        "Tests run: 1, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.02 s <<< FAILURE!\n"
        "\n"
        "Results:\n"
        "\n"
        "Tests run: 3, Failures: 1, Errors: 0, Skipped: 0\n"
    )
    ok, summary = a.parse_test_output(out, 1)
    assert not ok and "Failures: 1" in summary
    assert summary.startswith("Tests run: 3, Failures: 1")  # 最终汇总，而非第一个类
