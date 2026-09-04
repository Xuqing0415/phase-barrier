"""Dart 语言适配器测试（v0.40.0）：注册 / 检测 / 文件识别 / package:test 启发式统计 /
语法检查（mock 与真实 dart）/ 输出解析。"""
import shutil
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig
from anti_shortcut.languages import (
    LANGUAGE_REGISTRY,
    DartAdapter,
    detect_language,
    get_adapter,
)
from anti_shortcut.languages import dart as dart_module

DART_IMPL = """\
int fib(int n) {
  if (n <= 1) {
    return n;
  }
  var a = 0;
  var b = 1;
  for (var i = 2; i <= n; i++) {
    final tmp = a + b;
    a = b;
    b = tmp;
  }
  return b;
}
"""

DART_TESTS = """\
import 'package:test/test.dart';

void main() {
  test('base cases', () {
    expect(fib(0), 0);
    expect(fib(1), 1);
  });

  test('known value', () {
    expect(fib(10), 55);
  });

  group('negative', () {
    test('rejects negative', () {
      expect(() => fib(-1), throwsArgumentError);
    });
  });
}
"""

DART_BAD = """\
int fib(int n) {
  if (n <= 1) {
    return n
  }
}
"""


def _write(root: Path, name: str, content: str) -> Path:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------- 注册与检测 ----------


def test_dart_registered_and_detected(tmp_path):
    assert "dart" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["dart"] is DartAdapter
    _write(tmp_path, "pubspec.yaml", "name: demo\n")
    assert detect_language(tmp_path) == "dart"
    adapter = get_adapter(GateConfig(), workspace=tmp_path)
    assert isinstance(adapter, DartAdapter)


def test_dart_adapter_meta():
    ad = DartAdapter()
    assert ad.name == "dart"
    assert ".dart" in ad.file_extensions
    assert ad.source_file_patterns and ad.test_file_patterns
    assert any("test/**" in p for p in ad.test_file_patterns)


# ---------- 文件识别 ----------


def test_dart_classify_files(tmp_path):
    _write(tmp_path, "lib/fib.dart", DART_IMPL)
    _write(tmp_path, "test/fib_test.dart", DART_TESTS)
    _write(tmp_path, "bin/tool.dart", "void main() {}\n")
    _write(tmp_path, "web/app.dart", "void main() {}\n")
    _write(tmp_path, "integration_test/app_test.dart", DART_TESTS)
    ad = DartAdapter()
    assert ad.is_source_file(tmp_path / "lib/fib.dart")
    assert ad.is_source_file(tmp_path / "bin/tool.dart")
    assert ad.is_source_file(tmp_path / "web/app.dart")
    assert ad.is_test_file(tmp_path / "test/fib_test.dart")
    assert ad.is_test_file(tmp_path / "integration_test/app_test.dart")
    # 测试目录内实现不算 source；lib 内 _test 文件算测试
    assert not ad.is_source_file(tmp_path / "test/fib_test.dart")
    assert ad.is_test_file(tmp_path / "lib/helper_test.dart")


def test_dart_test_command_patterns():
    ad = DartAdapter()
    for cmd in ("dart test", "dart test test/", "flutter test", "dart run test", "pub run test"):
        assert ad.identify_test_command(cmd), cmd
    for cmd in ("python -m pytest", "go test", "npm test"):
        assert not ad.identify_test_command(cmd), cmd


# ---------- 启发式测试统计 ----------


def test_dart_analyze_counts_test_calls_and_expects(tmp_path):
    p = _write(tmp_path, "test/fib_test.dart", DART_TESTS)
    info = DartAdapter().analyze_tests(p)
    # 3 个 test() 声明（group 不计），3+1 expect
    assert len(info["test_functions"]) == 3
    assert info["assertions_total"] == 4


def test_dart_analyze_strips_comments_and_strings(tmp_path):
    text = """\
// test('in comment', () { expect(1, 1); });
/* test('block comment', () { expect(1, 1); }); */
const desc = "test('in string', () { expect(1, 1); });";
void main() {
  test('real', () {
    expect(1, 1);
  });
}
"""
    p = _write(tmp_path, "test/sample_test.dart", text)
    info = DartAdapter().analyze_tests(p)
    assert len(info["test_functions"]) == 1
    assert info["assertions_total"] == 1


def test_dart_analyze_testwidgets_flutter(tmp_path):
    text = """\
testWidgets('renders counter', (tester) async {
  expect(find.text('0'), findsOneWidget);
});
"""
    p = _write(tmp_path, "test/widget_test.dart", text)
    info = DartAdapter().analyze_tests(p)
    assert len(info["test_functions"]) == 1
    assert info["assertions_total"] == 1


# ---------- 语法检查（mock） ----------


def test_dart_check_syntax_empty_file(tmp_path):
    p = _write(tmp_path, "lib/empty.dart", "")
    ok, msg = DartAdapter().check_syntax(p)
    assert ok is False and "空文件" in msg


def test_dart_check_syntax_missing_sdk(tmp_path, monkeypatch):
    p = _write(tmp_path, "lib/fib.dart", DART_IMPL)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ok, msg = DartAdapter().check_syntax(p)
    assert ok is False and "Dart SDK" in msg and "dart" in msg


def test_dart_check_syntax_ok(tmp_path, monkeypatch):
    p = _write(tmp_path, "lib/fib.dart", DART_IMPL)

    class FakeProc:
        returncode = 0
        stdout = b""
        stderr = b""

    monkeypatch.setattr(shutil, "which", lambda name: "dart")
    monkeypatch.setattr(
        dart_module.subprocess,
        "run",
        lambda *a, **kw: FakeProc(),
    )
    ok, msg = DartAdapter().check_syntax(p)
    assert ok is True and "通过" in msg


def test_dart_check_syntax_error_reports_location(tmp_path, monkeypatch):
    p = _write(tmp_path, "lib/fib.dart", DART_BAD)

    class FakeProc:
        returncode = 1
        stdout = b""
        stderr = (
            b"lib/fib.dart:4:5: Error: Expected ';' after this.\n"
            b"    return n\n"
            b"        ^\n"
        )

    monkeypatch.setattr(shutil, "which", lambda name: "dart")
    monkeypatch.setattr(
        dart_module.subprocess,
        "run",
        lambda *a, **kw: FakeProc(),
    )
    ok, msg = DartAdapter().check_syntax(p)
    assert ok is False and "Dart 语法错误" in msg and "fib.dart:4" in msg


# ---------- 真实 dart SDK（CI ubuntu 激活 / 本机有则跑） ----------

needs_dart = pytest.mark.skipif(shutil.which("dart") is None, reason="dart 未安装")


@needs_dart
def test_dart_check_syntax_real_ok(tmp_path):
    p = _write(tmp_path, "lib/fib.dart", DART_IMPL)
    ok, msg = DartAdapter().check_syntax(p)
    assert ok is True, msg


@needs_dart
def test_dart_check_syntax_real_error(tmp_path):
    p = _write(tmp_path, "lib/bad.dart", DART_BAD)
    ok, msg = DartAdapter().check_syntax(p)
    assert ok is False and "Dart 语法错误" in msg


# ---------- 输出解析 ----------


def test_dart_parse_passed(tmp_path):
    out = "00:01 +5: All tests passed!\n"
    ok, summary = DartAdapter().parse_test_output(out, 0)
    assert ok is True and "5" in summary and "全部通过" in summary


def test_dart_parse_passed_with_skips():
    out = "00:02 +3 ~1: All tests passed!\n"
    ok, summary = DartAdapter().parse_test_output(out, 0)
    assert ok is True and "3" in summary and "1 个跳过" in summary


def test_dart_parse_failed_counts():
    out = "00:05 +2 -1: Some tests failed.\n"
    ok, summary = DartAdapter().parse_test_output(out, 1)
    assert ok is False and "2" in summary and "1" in summary


def test_dart_parse_no_progress_line():
    assert DartAdapter().parse_test_output("some noise", 0)[0] is True
    ok, summary = DartAdapter().parse_test_output("boom", 2)
    assert ok is False and "退出码 2" in summary