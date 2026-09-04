"""Swift 语言适配器测试（v0.37.0）：注册 / 检测 / 文件识别 / XCTest 与 swift-testing
启发式 / 语法检查 / 输出解析。"""
import shutil
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig
from anti_shortcut.languages import (
    LANGUAGE_REGISTRY,
    SwiftAdapter,
    detect_language,
    get_adapter,
)
from anti_shortcut.languages import swift as swift_module

SWIFT_IMPL = """\
func fib(_ n: Int) -> Int {
    if n <= 1 {
        return n
    }
    var a = 0
    var b = 1
    for _ in 2...n {
        let tmp = a + b
        a = b
        b = tmp
    }
    return b
}
"""

SWIFT_XCTEST_TESTS = """\
import XCTest

final class FibTests: XCTestCase {
    func testBaseCases() {
        XCTAssertEqual(fib(0), 0)
        XCTAssertEqual(fib(1), 1)
    }

    func testKnownValue() {
        XCTAssertEqual(fib(10), 55)
    }

    func testInvalidInputThrows() {
        XCTAssertNotEqual(fib(-1), 0)
    }
}
"""

SWIFT_TESTING_TESTS = """\
import Testing

struct FibTests {
    @Test
    func knownValue() {
        #expect(fib(10) == 55)
    }

    @Test(arguments: [0, 1, 10])
    func matchesReference(n: Int) {
        #expect(fib(n) == reference(n))
    }
}
"""

needs_swiftc = pytest.mark.skipif(
    shutil.which("swiftc") is None, reason="swiftc 未安装"
)


# ---------- 注册与检测 ----------

def test_swift_adapter_registered():
    assert "swift" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["swift"] is SwiftAdapter


def test_swift_adapter_detected_via_package_swift(tmp_path):
    (tmp_path / "Package.swift").write_text(
        '// swift-tools-version: 5.9\nimport PackageDescription\n', encoding="utf-8"
    )
    assert detect_language(tmp_path) == "swift"
    assert isinstance(get_adapter(GateConfig(), tmp_path), SwiftAdapter)


def test_swift_adapter_explicit_config(tmp_path):
    adapter = get_adapter(GateConfig(language="swift"), tmp_path)
    assert isinstance(adapter, SwiftAdapter)


def test_swift_adapter_package_swift_not_source(tmp_path):
    a = SwiftAdapter()
    assert not a.is_source_file(Path("Package.swift"))
    assert not a.is_test_file(Path("Package.swift"))


# ---------- 文件识别 ----------

def test_swift_adapter_file_classification():
    a = SwiftAdapter()
    assert a.is_source_file(Path("Sources/Fib/Fib.swift"))
    assert a.is_source_file(Path("Sources/App/main.swift"))
    assert a.is_source_file(Path("App/Models/Fib.swift"))
    assert a.is_source_file(Path("Fib.swift"))
    assert not a.is_source_file(Path("Tests/FibTests/FibTests.swift"))
    assert not a.is_source_file(Path("FibTests.swift"))
    assert not a.is_source_file(Path("FibTest.swift"))
    assert a.is_test_file(Path("FibTests.swift"))
    assert a.is_test_file(Path("FibTest.swift"))
    assert a.is_test_file(Path("Tests/FibTests/FibTests.swift"))
    assert a.is_test_file(Path("test/FibTests.swift"))
    assert a.is_test_file(Path("spec/FibTests.swift"))
    assert not a.is_test_file(Path("Sources/Fib/Fib.swift"))
    assert not a.is_source_file(Path("README.md"))
    assert not a.is_test_file(Path("README.md"))


# ---------- 测试统计（启发式） ----------

def test_swift_analyze_xctest(tmp_path):
    f = tmp_path / "FibTests.swift"
    f.write_text(SWIFT_XCTEST_TESTS, encoding="utf-8")
    info = SwiftAdapter().analyze_tests(f)
    names = [t["name"] for t in info["test_functions"]]
    assert len(names) == 3
    assert names == ["<1:testBaseCases>", "<2:testKnownValue>", "<3:testInvalidInputThrows>"]
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 4


def test_swift_analyze_swift_testing(tmp_path):
    f = tmp_path / "FibTests.swift"
    f.write_text(SWIFT_TESTING_TESTS, encoding="utf-8")
    info = SwiftAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["assertions_total"] >= 2
    assert all(t["heuristic"] for t in info["test_functions"])


def test_swift_analyze_empty(tmp_path):
    f = tmp_path / "EmptyTest.swift"
    f.write_text("struct EmptyTest {}\n", encoding="utf-8")
    info = SwiftAdapter().analyze_tests(f)
    assert info["test_functions"] == []
    assert info["assertions_total"] == 0


def test_swift_analyze_ignores_comments(tmp_path):
    f = tmp_path / "CommentTest.swift"
    f.write_text(
        "// func testFake() {\n"
        "/* func testBlockFake() { XCTAssertEqual(1, 1) } */\n"
        "struct RealTest {\n"
        "    func testReal() {\n"
        "        XCTAssertEqual(2, 2)\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    info = SwiftAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 1
    assert info["test_functions"][0]["name"] == "<1:testReal>"
    assert info["assertions_total"] == 1


def test_swift_analyze_ignores_strings(tmp_path):
    f = tmp_path / "DocTest.swift"
    f.write_text(
        'let doc = "func testDoc() { #expect(fib(1) == 1) }"\n'
        'let multi = """\nfunc testMultiline() {\n    #expect(fib(2) == 1)\n}\n"""\n'
        "struct RealTest {\n"
        "    func testReal() {\n"
        "        #expect(fib(3) == 2)\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    info = SwiftAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 1
    assert info["test_functions"][0]["name"] == "<1:testReal>"
    assert info["assertions_total"] == 1


def test_swift_analyze_nested_block_comment(tmp_path):
    f = tmp_path / "NestedTest.swift"
    f.write_text(
        "/* outer /* func testInner() { #expect(1 == 1) } */ still comment */\n"
        "func testOuter() {\n"
        "    #expect(1 == 1)\n"
        "}\n",
        encoding="utf-8",
    )
    info = SwiftAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 1
    assert info["test_functions"][0]["name"] == "<1:testOuter>"
    assert info["assertions_total"] == 1


# ---------- 语法检查 ----------

@needs_swiftc
def test_swift_check_syntax_valid(tmp_path):
    f = tmp_path / "Fib.swift"
    f.write_text(SWIFT_IMPL, encoding="utf-8")
    ok, msg = SwiftAdapter().check_syntax(f)
    assert ok is True
    assert "语法检查通过" in msg


@needs_swiftc
def test_swift_check_syntax_reports_error(tmp_path):
    f = tmp_path / "broken.swift"
    f.write_text("func fib( {\n", encoding="utf-8")
    ok, msg = SwiftAdapter().check_syntax(f)
    assert ok is False
    assert "Swift 语法错误" in msg


@needs_swiftc
def test_swift_check_syntax_dependency_downgrade(tmp_path):
    # 单文件无法解析跨文件符号（cannot find in scope）→ 降级为“通过，需项目级编译验证”
    f = tmp_path / "uses_fib.swift"
    f.write_text("let value = fib(10)\n", encoding="utf-8")
    ok, msg = SwiftAdapter().check_syntax(f)
    assert ok is True
    assert "语法检查通过" in msg


def test_swift_check_syntax_empty_file(tmp_path):
    f = tmp_path / "empty.swift"
    f.write_text("", encoding="utf-8")
    ok, msg = SwiftAdapter().check_syntax(f)
    assert ok is False and "空文件" in msg


def test_swift_check_syntax_missing_swiftc(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    f = tmp_path / "x.swift"
    f.write_text("func x() -> Int { 1 }\n", encoding="utf-8")
    ok, msg = SwiftAdapter().check_syntax(f)
    assert ok is False and "swiftc" in msg


class _FakeSwiftcResult:
    def __init__(self, returncode: int, stderr: str) -> None:
        self.returncode = returncode
        self.stderr = stderr.encode("utf-8")
        self.stdout = b""


def _fake_run(results: dict):
    """构造按 flags 返回不同结果的 _run_swiftc 假实现。"""
    def run(swiftc: str, path: Path, *flags: str):
        for key, (rc, err) in results.items():
            if key == "lib" and "-parse-as-library" in flags:
                return _FakeSwiftcResult(rc, err)
            if key == "script" and "-parse-as-library" not in flags:
                return _FakeSwiftcResult(rc, err)
        raise AssertionError(f"unexpected flags: {flags}")
    return run


def test_swift_check_syntax_retry_parse_as_library(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "swiftc")
    f = tmp_path / "main.swift"
    f.write_text("@main\nstruct Runner {\n    static func main() {}\n}\n", encoding="utf-8")
    monkeypatch.setattr(
        swift_module,
        "_run_swiftc",
        _fake_run({
            "script": (1, "main.swift:1:1: error: 'main' attribute cannot be used in a module that contains top-level code"),
            "lib": (0, ""),
        }),
    )
    ok, msg = SwiftAdapter().check_syntax(f)
    assert ok is True
    assert "parse-as-library" in msg


def test_swift_check_syntax_retry_still_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "swiftc")
    # @main 重试后仍存在真实语法错误 → 拒绝
    f = tmp_path / "main.swift"
    f.write_text("@main\nstruct Runner {\n", encoding="utf-8")
    monkeypatch.setattr(
        swift_module,
        "_run_swiftc",
        _fake_run({
            "script": (1, "main.swift:1:1: error: 'main' attribute cannot be used in a module that contains top-level code"),
            "lib": (1, "main.swift:3:1: error: expected '}' to end struct"),
        }),
    )
    ok, msg = SwiftAdapter().check_syntax(f)
    assert ok is False
    assert "Swift 语法错误" in msg


def test_swift_check_syntax_all_dependency_errors_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "swiftc")
    # 全部报错均为跨文件依赖缺失 → 降级通过
    f = tmp_path / "uses_stdlib.swift"
    f.write_text("import CoreGraphics\nlet p = CGPoint.zero\n", encoding="utf-8")
    monkeypatch.setattr(
        swift_module,
        "_run_swiftc",
        lambda swiftc, path, *flags: _FakeSwiftcResult(
            1, "uses_stdlib.swift:1:8: error: cannot build module 'CoreGraphics'"
        ),
    )
    ok, msg = SwiftAdapter().check_syntax(f)
    assert ok is True
    assert "语法检查通过" in msg


# ---------- 测试命令识别 ----------

def test_swift_identify_test_command():
    a = SwiftAdapter()
    assert a.identify_test_command("swift test")
    assert a.identify_test_command("swift test --parallel")
    assert a.identify_test_command("xcodebuild test -scheme App")
    assert a.identify_test_command("xcrun xctest .build/debug/AppTests.xctest")
    assert a.identify_test_command("xctest")
    assert not a.identify_test_command("swift run")
    assert not a.identify_test_command("python -m pytest")
    assert not a.identify_test_command("ls -la")


# ---------- 输出解析 ----------

def test_swift_parse_xctest_passed():
    out = (
        "Test Suite 'All tests' started at ...\n"
        "Test Suite 'FibTests.xctest' passed at ...\n"
        "\tExecuted 3 tests, with 0 failures (0 unexpected) in 0.01 seconds\n"
        "Test Suite 'All tests' passed at ...\n"
        "\tExecuted 3 tests, with 0 failures (0 unexpected) in 0.01 seconds\n"
    )
    ok, summary = SwiftAdapter().parse_test_output(out, 0)
    assert ok is True
    assert "Executed 3 tests, with 0 failures" in summary


def test_swift_parse_xctest_failed():
    out = (
        "Test Case '-[FibTests testKnownValue]' failed (0.01 seconds).\n"
        "error: -[FibTests testKnownValue] : XCTAssertEqual failed\n"
        "\tExecuted 3 tests, with 1 failure (1 unexpected) in 0.01 seconds\n"
    )
    ok, summary = SwiftAdapter().parse_test_output(out, 1)
    assert ok is False
    assert "1 failures" in summary


def test_swift_parse_xctest_last_aggregate_wins():
    # 多套件输出：取最后一次 Executed 汇总（总套件）
    out = (
        "Test Suite 'FibTests' passed\n"
        "\tExecuted 1 test, with 0 failures (0 unexpected)\n"
        "Test Suite 'All tests' passed\n"
        "\tExecuted 1 test, with 0 failures (0 unexpected)\n"
    )
    ok, _ = SwiftAdapter().parse_test_output(out, 0)
    assert ok is True


def test_swift_parse_swift_testing_passed():
    out = "Test run with 3 tests passed.\n"
    ok, summary = SwiftAdapter().parse_test_output(out, 0)
    assert ok is True
    assert "swift-testing" in summary


def test_swift_parse_swift_testing_failed():
    out = "Test run with 1 test failed.\n"
    ok, summary = SwiftAdapter().parse_test_output(out, 1)
    assert ok is False
    assert "failed" in summary


def test_swift_parse_xcodebuild_succeeded():
    out = "** TEST SUCCEEDED **\n"
    ok, _ = SwiftAdapter().parse_test_output(out, 0)
    assert ok is True


def test_swift_parse_xcodebuild_failed():
    out = "** TEST FAILED **\n"
    ok, summary = SwiftAdapter().parse_test_output(out, 1)
    assert ok is False
    assert "xcodebuild" in summary


def test_swift_parse_failed_case_anchor():
    # 无 Executed 汇总但存在失败用例行 → 失败
    out = "Test Case '-[FibTests testKnownValue]' failed (0.01 seconds).\n"
    ok, summary = SwiftAdapter().parse_test_output(out, 1)
    assert ok is False
    assert "XCTest" in summary


def test_swift_parse_empty_failed():
    ok, summary = SwiftAdapter().parse_test_output("", 2)
    assert ok is False and "退出码 2" in summary


# ---------- 内部聚合函数 ----------

def test_swift_aggregate_helpers():
    assert swift_module._xctest_aggregate("") is None
    assert swift_module._xctest_aggregate("Executed 1 test, with 0 failures") == (1, 0)
    assert swift_module._xctest_aggregate(
        "Executed 2 tests, with 1 failure\nExecuted 5 tests, with 0 failures"
    ) == (5, 0)
    assert swift_module._swift_testing_run("nothing here") is None
    assert swift_module._swift_testing_run("Test run with 2 tests failed.") == (2, "failed")
