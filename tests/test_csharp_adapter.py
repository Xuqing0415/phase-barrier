"""C# 语言适配器测试：注册 / 检测 / 文件识别 / dotnet build / dotnet test 解析。"""
import shutil
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.languages import CSharpAdapter, LANGUAGE_REGISTRY, detect_language, get_adapter
from anti_shortcut.validators import validate_tests

CS_PROJ = '''<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
    <Nullable>disable</Nullable>
  </PropertyGroup>
</Project>
'''

needs_dotnet = pytest.mark.skipif(
    shutil.which("dotnet") is None, reason="dotnet SDK 未安装"
)

CS_IMPL = """\
namespace Fib
{
    public static class Fibonacci
    {
        public static long Calc(int n) => n <= 1 ? n : Calc(n - 1) + Calc(n - 2);
    }
}
"""

CS_TESTS = """\
using Xunit;

namespace Fib.Tests
{
    public class FibonacciTests
    {
        [Fact]
        public void BaseCases()
        {
            Assert.Equal(0, Fibonacci.Calc(0));
            Assert.Equal(1, Fibonacci.Calc(1));
        }

        [Fact]
        public void KnownValue()
        {
            Assert.Equal(55, Fibonacci.Calc(10));
        }
    }
}
"""

CS_NUNIT_TESTS = """\
using NUnit.Framework;

public class FibonacciTest
{
    [Test]
    public void BaseCases()
    {
        Assert.AreEqual(0, Fibonacci.Calc(0));
        Assert.IsTrue(Fibonacci.Calc(1) == 1);
    }
}
"""


# ---------- 注册与检测 ----------

def test_csharp_adapter_registered():
    assert "csharp" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["csharp"] is CSharpAdapter


def test_csharp_adapter_detected_via_csproj(tmp_path):
    (tmp_path / "App.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk" />', encoding="utf-8")
    assert detect_language(tmp_path) == "csharp"
    assert isinstance(get_adapter(GateConfig(), tmp_path), CSharpAdapter)


def test_csharp_adapter_detected_via_sln(tmp_path):
    (tmp_path / "App.sln").write_text("Microsoft Visual Studio Solution File", encoding="utf-8")
    assert detect_language(tmp_path) == "csharp"


# ---------- 文件识别 ----------

def test_csharp_adapter_file_classification():
    a = CSharpAdapter()
    assert a.is_test_file(Path("FibonacciTests.cs"))
    assert a.is_test_file(Path("FibonacciTest.cs"))
    assert a.is_test_file(Path("Tests/FibonacciTests.cs"))
    assert not a.is_test_file(Path("src/Fibonacci.cs"))
    assert a.is_source_file(Path("src/Fibonacci.cs"))
    assert a.is_source_file(Path("Program.cs"))
    assert not a.is_source_file(Path("FibonacciTests.cs"))
    assert not a.is_source_file(Path("README.md"))


# ---------- 测试统计（启发式） ----------

def test_csharp_adapter_analyze_xunit(tmp_path):
    f = tmp_path / "FibonacciTests.cs"
    f.write_text(CS_TESTS, encoding="utf-8")
    info = CSharpAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 2


def test_csharp_adapter_analyze_nunit(tmp_path):
    f = tmp_path / "FibonacciTest.cs"
    f.write_text(CS_NUNIT_TESTS, encoding="utf-8")
    info = CSharpAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 1
    assert info["assertions_total"] >= 2


def test_csharp_adapter_analyze_empty(tmp_path):
    f = tmp_path / "EmptyTests.cs"
    f.write_text("using Xunit;\n", encoding="utf-8")
    info = CSharpAdapter().analyze_tests(f)
    assert info["test_functions"] == []
    assert info["assertions_total"] == 0


# ---------- 语法检查 ----------

def test_csharp_adapter_check_syntax_empty_file(tmp_path):
    f = tmp_path / "Empty.cs"
    f.write_text("", encoding="utf-8")
    ok, msg = CSharpAdapter().check_syntax(f)
    assert not ok and "空文件" in msg


def test_csharp_adapter_check_syntax_no_project(tmp_path):
    f = tmp_path / "Fib.cs"
    f.write_text(CS_IMPL, encoding="utf-8")
    ok, msg = CSharpAdapter().check_syntax(f)
    assert not ok and "项目根" in msg


def test_csharp_adapter_check_syntax_missing_dotnet(tmp_path, monkeypatch):
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    f = tmp_path / "Fib.cs"
    f.write_text(CS_IMPL, encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.csharp.shutil.which", lambda name: None)
    ok, msg = CSharpAdapter().check_syntax(f)
    assert not ok and ".NET SDK" in msg


def test_csharp_adapter_check_syntax_project_ok(tmp_path, monkeypatch):
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    f = tmp_path / "Fib.cs"
    f.write_text(CS_IMPL, encoding="utf-8")

    class FakeProc:
        returncode = 0
        stderr = ""
        stdout = "Build succeeded."

    monkeypatch.setattr("anti_shortcut.languages.csharp.subprocess.run", lambda *a, **k: FakeProc())
    monkeypatch.setattr("anti_shortcut.languages.csharp.shutil.which", lambda name: "dotnet")
    ok, msg = CSharpAdapter().check_syntax(f)
    assert ok and "dotnet build" in msg


def test_csharp_adapter_check_syntax_project_error(tmp_path, monkeypatch):
    (tmp_path / "App.csproj").write_text("<Project />", encoding="utf-8")
    f = tmp_path / "Fib.cs"
    f.write_text(CS_IMPL, encoding="utf-8")

    class FakeProc:
        returncode = 1
        stderr = "Fib.cs(3,12): error CS1002: ; expected"
        stdout = ""

    monkeypatch.setattr("anti_shortcut.languages.csharp.subprocess.run", lambda *a, **k: FakeProc())
    monkeypatch.setattr("anti_shortcut.languages.csharp.shutil.which", lambda name: "dotnet")
    ok, msg = CSharpAdapter().check_syntax(f)
    assert not ok and "CS1002" in msg


# ---------- 测试命令识别 ----------

def test_csharp_adapter_identify_test_command():
    a = CSharpAdapter()
    assert a.identify_test_command("dotnet test")
    assert a.identify_test_command("dotnet test -p:CollectCoverage=true")
    assert a.identify_test_command("dotnet vstest Tests.dll")
    assert a.identify_test_command("nunit3-console Tests.dll")
    assert not a.identify_test_command("dotnet build")
    assert not a.identify_test_command("dotnet run")
    assert not a.identify_test_command("ls -la")


# ---------- 测试输出解析 ----------

def test_csharp_adapter_parse_dotnet_test_output():
    a = CSharpAdapter()
    ok, summary = a.parse_test_output("Passed! - Failed: 0, Passed: 3, Skipped: 0, Total: 3", 0)
    assert ok and "Passed: 3" in summary
    ok2, summary2 = a.parse_test_output("Failed! - Failed: 1, Passed: 2, Skipped: 0, Total: 3", 1)
    assert not ok2 and "Failed: 1" in summary2


def test_csharp_adapter_parse_nunit_output():
    a = CSharpAdapter()
    ok, summary = a.parse_test_output("Overall result: Passed", 0)
    assert ok and "NUnit" in summary
    ok2, summary2 = a.parse_test_output("Overall result: Failed", 1)
    assert not ok2 and "Failed" in summary2


def test_csharp_adapter_parse_build_failure():
    a = CSharpAdapter()
    ok, summary = a.parse_test_output("Build FAILED.\nerror CS1002: ; expected", 1)
    assert not ok and "编译失败" in summary


# ---------- 真实工具链（CI 已安装 .NET SDK） ----------

@needs_dotnet
def test_csharp_adapter_real_dotnet_build_ok(tmp_path):
    (tmp_path / "App.csproj").write_text(CS_PROJ, encoding="utf-8")
    f = tmp_path / "Fib.cs"
    f.write_text(CS_IMPL, encoding="utf-8")
    ok, msg = CSharpAdapter().check_syntax(f)
    assert ok, msg
    assert "dotnet build" in msg


@needs_dotnet
def test_csharp_adapter_real_dotnet_build_error(tmp_path):
    (tmp_path / "App.csproj").write_text(CS_PROJ, encoding="utf-8")
    f = tmp_path / "Broken.cs"
    f.write_text("namespace Fib { public class Bad { public void M( { } } }", encoding="utf-8")
    ok, msg = CSharpAdapter().check_syntax(f)
    assert not ok and "编译错误" in msg


# ---------- 校验器接线 ----------

def test_validate_tests_csharp_with_language_config(tmp_path):
    p = tmp_path / "Tests"
    p.mkdir(parents=True)
    (p / "FibonacciTests.cs").write_text(CS_TESTS, encoding="utf-8")
    cfg = load_config({"language": "csharp"})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok, msg
