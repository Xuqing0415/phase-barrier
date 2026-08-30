"""Ruby 语言适配器测试：注册 / 检测 / 文件识别 / ruby -c / RSpec·Minitest 解析。"""
import shutil
from pathlib import Path

import pytest

from anti_shortcut.config import GateConfig, load_config
from anti_shortcut.languages import LANGUAGE_REGISTRY, RubyAdapter, detect_language, get_adapter
from anti_shortcut.validators import validate_tests

RUBY_IMPL = """\
def fib(n)
  n <= 1 ? n : fib(n - 1) + fib(n - 2)
end
"""

RUBY_RSPEC = """\
require "fib"

RSpec.describe Fib do
  describe "#fib" do
    it "returns 0 and 1 for base cases" do
      expect(Fib.fib(0)).to eq(0)
      expect(Fib.fib(1)).to eq(1)
    end

    it "returns 55 for fib(10)" do
      expect(Fib.fib(10)).to eq(55)
    end
  end
end
"""

RUBY_MINITEST = """\
require "minitest/autorun"
require "fib"

class FibTest < Minitest::Test
  def test_base_cases
    assert_equal 0, Fib.fib(0)
    assert_equal 1, Fib.fib(1)
  end

  def test_known_value
    assert_equal 55, Fib.fib(10)
  end
end
"""

needs_ruby = pytest.mark.skipif(
    shutil.which("ruby") is None, reason="Ruby 未安装"
)


# ---------- 注册与检测 ----------

def test_ruby_adapter_registered():
    assert "ruby" in LANGUAGE_REGISTRY
    assert LANGUAGE_REGISTRY["ruby"] is RubyAdapter


def test_ruby_adapter_detected_via_gemfile(tmp_path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    assert detect_language(tmp_path) == "ruby"
    assert isinstance(get_adapter(GateConfig(), tmp_path), RubyAdapter)


def test_ruby_adapter_detected_via_gemspec(tmp_path):
    (tmp_path / "fib.gemspec").write_text(
        "Gem::Specification.new { |s| s.name = 'fib' }\n", encoding="utf-8"
    )
    assert detect_language(tmp_path) == "ruby"


# ---------- 文件识别 ----------

def test_ruby_adapter_file_classification():
    a = RubyAdapter()
    assert a.is_test_file(Path("spec/fib_spec.rb"))
    assert a.is_test_file(Path("fib_spec.rb"))
    assert a.is_test_file(Path("test/fib_test.rb"))
    assert a.is_test_file(Path("fib_test.rb"))
    assert not a.is_test_file(Path("lib/fib.rb"))
    assert a.is_source_file(Path("lib/fib.rb"))
    assert a.is_source_file(Path("app/models/fib.rb"))
    assert not a.is_source_file(Path("spec/fib_spec.rb"))
    assert not a.is_source_file(Path("README.md"))


# ---------- 测试统计（启发式） ----------

def test_ruby_adapter_analyze_rspec(tmp_path):
    f = tmp_path / "fib_spec.rb"
    f.write_text(RUBY_RSPEC, encoding="utf-8")
    info = RubyAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["heuristic"] is True
    assert info["assertions_total"] >= 2


def test_ruby_adapter_analyze_minitest(tmp_path):
    f = tmp_path / "fib_test.rb"
    f.write_text(RUBY_MINITEST, encoding="utf-8")
    info = RubyAdapter().analyze_tests(f)
    assert len(info["test_functions"]) == 2
    assert info["assertions_total"] >= 2


def test_ruby_adapter_analyze_empty(tmp_path):
    f = tmp_path / "empty_spec.rb"
    f.write_text("require 'spec_helper'\n", encoding="utf-8")
    info = RubyAdapter().analyze_tests(f)
    assert info["test_functions"] == []
    assert info["assertions_total"] == 0


# ---------- 语法检查 ----------

def test_ruby_adapter_check_syntax_empty_file(tmp_path):
    f = tmp_path / "Empty.rb"
    f.write_text("", encoding="utf-8")
    ok, msg = RubyAdapter().check_syntax(f)
    assert not ok and "空文件" in msg


def test_ruby_adapter_check_syntax_missing_ruby(tmp_path, monkeypatch):
    f = tmp_path / "fib.rb"
    f.write_text(RUBY_IMPL, encoding="utf-8")
    monkeypatch.setattr("anti_shortcut.languages.ruby.shutil.which", lambda name: None)
    ok, msg = RubyAdapter().check_syntax(f)
    assert not ok and "Ruby" in msg


@needs_ruby
def test_ruby_adapter_check_syntax_ok(tmp_path):
    f = tmp_path / "fib.rb"
    f.write_text(RUBY_IMPL, encoding="utf-8")
    ok, msg = RubyAdapter().check_syntax(f)
    assert ok and "ruby -c" in msg


@needs_ruby
def test_ruby_adapter_check_syntax_error(tmp_path):
    f = tmp_path / "Broken.rb"
    f.write_text("def fib(n)\n  n <= 1 ? n : \nend\n", encoding="utf-8")
    ok, msg = RubyAdapter().check_syntax(f)
    assert not ok and "Ruby 语法错误" in msg


# ---------- 测试命令识别 ----------

def test_ruby_adapter_identify_test_command():
    a = RubyAdapter()
    assert a.identify_test_command("rspec")
    assert a.identify_test_command("bundle exec rspec spec/")
    assert a.identify_test_command("rake test")
    assert a.identify_test_command("rails test")
    assert a.identify_test_command("bin/rails test")
    assert a.identify_test_command("ruby -Itest test/fib_test.rb")
    assert a.identify_test_command("bundle exec ruby -Itest test/fib_test.rb")
    assert not a.identify_test_command("ruby app.rb")
    assert not a.identify_test_command("bundle install")
    assert not a.identify_test_command("ls -la")


# ---------- 测试输出解析 ----------

def test_ruby_adapter_parse_rspec_output():
    a = RubyAdapter()
    ok, summary = a.parse_test_output("Finished in 0.01 seconds\n2 examples, 0 failures", 0)
    assert ok and "2 examples, 0 failures" in summary
    ok2, summary2 = a.parse_test_output(
        "2 examples, 1 failure\n\nFailures:\n  1) Fib#fib returns 55 for fib(10)", 1
    )
    assert not ok2 and "1 failure" in summary2


def test_ruby_adapter_parse_minitest_output():
    a = RubyAdapter()
    ok, summary = a.parse_test_output(
        "Run options: --seed 1\n\n5 runs, 5 assertions, 0 failures, 0 errors, 0 skips", 0
    )
    assert ok and "5 runs" in summary
    ok2, summary2 = a.parse_test_output("5 runs, 5 assertions, 1 failures, 0 errors, 0 skips", 1)
    assert not ok2 and "1 failures" in summary2


def test_ruby_adapter_parse_unknown_output():
    a = RubyAdapter()
    ok, summary = a.parse_test_output("whatever", 0)
    assert ok
    ok2, _ = a.parse_test_output("whatever", 1)
    assert not ok2


# ---------- 校验器接线 ----------

def test_validate_tests_ruby_with_language_config(tmp_path):
    p = tmp_path / "spec"
    p.mkdir(parents=True)
    (p / "fib_spec.rb").write_text(RUBY_RSPEC, encoding="utf-8")
    cfg = load_config({"language": "ruby"})
    ok, msg, ev = validate_tests(tmp_path, cfg, None)
    assert ok, msg
