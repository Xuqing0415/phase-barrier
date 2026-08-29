"""工具拦截辅助模块测试。"""
from pathlib import Path

from anti_shortcut.config import GateConfig
from anti_shortcut.interceptors import (
    extract_written_paths,
    is_language_test_command,
    is_test_command,
    summarize_test_output,
    touches_gate_dir,
)


def test_is_test_command():
    cfg = GateConfig()
    assert is_test_command("pytest -q", cfg)
    assert is_test_command("python -m pytest tests/", cfg)
    assert is_test_command("python3 -m pytest", cfg)
    assert is_test_command("npm test", cfg)
    assert is_test_command("npx jest", cfg)
    assert is_test_command("cargo test", cfg)
    assert not is_test_command("ls -la", cfg)
    assert not is_test_command("cat spec.md", cfg)
    assert not is_test_command("", cfg)


def test_touches_gate_dir():
    gate = Path("ws/.agent_gate")
    assert touches_gate_dir("rm -rf .agent_gate", gate)
    assert touches_gate_dir("cat .agent_gate/state.json", gate)
    assert touches_gate_dir("cd .agent_gate && rm state.json", gate)
    assert touches_gate_dir("echo x > .agent_gate/state.json", gate)
    assert not touches_gate_dir("ls -la", gate)
    assert not touches_gate_dir("pytest", gate)


def test_extract_written_paths():
    assert extract_written_paths("echo hello > fib.py") == ["fib.py"]
    assert extract_written_paths("cmd >> test_fib.py") == ["test_fib.py"]
    assert extract_written_paths("sed -i s/a/b/ fib.py") == ["fib.py"]
    assert extract_written_paths("sed -i -e s/a/b/ fib.py") == ["fib.py"]
    assert extract_written_paths("mv a.py tests/test_b.py") == ["tests/test_b.py"]
    assert extract_written_paths("cp x.py y.py") == ["y.py"]
    assert extract_written_paths("rm -rf junk.py") == ["junk.py"]
    assert extract_written_paths("touch spec.md") == ["spec.md"]
    assert extract_written_paths("ls -la") == []
    assert extract_written_paths("cat spec.md") == []


def test_summarize_test_output():
    rec = summarize_test_output("..F\n1 failed, 2 passed in 0.1s", 1)
    assert rec["passed"] is False
    assert rec["exit_code"] == 1
    assert "1 failed" in rec["summary"]

    rec2 = summarize_test_output("3 passed in 0.1s", 0)
    assert rec2["passed"] is True
    assert "3 passed" in rec2["summary"]

    rec3 = summarize_test_output("2 failed", None)
    assert rec3["passed"] is False

    rec4 = summarize_test_output("5 passed", None)
    assert rec4["passed"] is True

def test_is_test_command_edge_cases():
    cfg = GateConfig()
    assert is_test_command("npm test -- --runInBand", cfg)
    assert is_test_command("python -m pytest tests/test_x.py -q", cfg)
    assert is_test_command("NPM TEST", cfg)  # 忽略大小写
    assert not is_test_command("npm run build", cfg)
    assert not is_test_command("python fib.py", cfg)
    assert not is_test_command(None, cfg)


def test_extract_written_paths_edge_cases():
    assert extract_written_paths('echo hi > "my file.py"') == ["my file.py"]
    assert extract_written_paths("printf 'x' > out.ts") == ["out.ts"]
    assert extract_written_paths("tee logs.txt") == ["logs.txt"]
    assert extract_written_paths("cp src/a.py tests/b.py") == ["tests/b.py"]
    assert extract_written_paths("install -m 644 a.py b.py") == ["b.py"]
    assert extract_written_paths("cat > x.py <<EOF") == ["x.py"]
    assert extract_written_paths("ls -la") == []


def test_touches_gate_dir_edge_cases():
    gate = Path("ws/.agent_gate")
    assert touches_gate_dir('rm -rf ".agent_gate"', gate)
    assert touches_gate_dir("type .agent_gate/state.json", gate)
    assert not touches_gate_dir(".agent_gate_extra", gate)
    assert not touches_gate_dir("", gate)


def test_summarize_test_output_edge_cases():
    rec = summarize_test_output("1 failed, 3 passed in 0.1s", None)
    assert rec["passed"] is False and "1 failed" in rec["summary"]
    rec2 = summarize_test_output("", 0)
    assert rec2["passed"] is True and rec2["summary"] == ""
    rec3 = summarize_test_output("All tests passed", None)
    assert rec3["passed"] is True
    rec4 = summarize_test_output("ERROR: cannot import fib", 1)
    assert rec4["passed"] is False


# ---------- v0.3.1 新增：拦截器边界 ----------

def test_is_language_test_command_injection():
    """命令注入写法（分号/逻辑符串联）应被识别为测试命令，宁可多拦。"""
    cfg = GateConfig()
    assert is_language_test_command("pytest -q; rm -rf /", cfg)
    assert is_language_test_command("npm test && git push origin main", cfg)
    assert is_language_test_command("python -m pytest tests/ && echo done", cfg)
    assert not is_language_test_command("", cfg)
    assert not is_language_test_command(None, cfg)


def test_is_language_test_command_keyword_fallback():
    """关键词兜底：独立 test 单词（make test / 自定义脚本）也能识别。"""
    cfg = GateConfig()
    assert is_language_test_command("make test", cfg)
    assert is_language_test_command("./test", cfg)  # 自定义测试脚本
    assert is_language_test_command("npx test", cfg)
    assert not is_language_test_command("python fib.py", cfg)
    assert not is_language_test_command("ls test_dir", cfg)  # test 不是独立单词


def test_is_language_test_command_config_pattern():
    """自定义 test_commands 正则可覆盖默认规则。"""
    cfg = GateConfig(test_commands=[r"^\s*run-my-tests\b"])
    assert is_language_test_command("run-my-tests -v", cfg)
    assert not is_language_test_command("pytest", cfg)


def test_touches_gate_dir_path_segments():
    """门禁目录路径段匹配：$HOME / 绝对路径 / 相对路径变体。"""
    gate = Path("ws/.agent_gate")
    assert touches_gate_dir("cat $HOME/.agent_gate/state.json", gate)
    assert touches_gate_dir("rm -rf /tmp/.agent_gate", gate)
    assert touches_gate_dir("type C:/ws/.agent_gate/state.json", gate)
    assert touches_gate_dir("./.agent_gate/state.json", gate)
    assert not touches_gate_dir("cat /var/log/syslog", gate)
    assert not touches_gate_dir("cat .agent_gate_extra", gate)


def test_extract_written_paths_dd():
    """dd 的 of= 目标应被识别为写路径（如 dd 覆写实现/状态文件）。"""
    assert extract_written_paths("dd if=/dev/zero of=fib.py bs=1024 count=1") == ["fib.py"]
    assert extract_written_paths("dd of=out.ts") == ["out.ts"]
    assert extract_written_paths("dd of=fib.py oflag=append") == ["fib.py"]
    assert extract_written_paths("dd if=/dev/zero") == []


def test_extract_written_paths_tee_and_quotes():
    """tee / 引号路径的写路径提取。"""
    assert extract_written_paths('tee -a "my file.txt"') == ["my file.txt"]
    assert extract_written_paths("echo hi > 'dir/my file.py'") == ["dir/my file.py"]
    assert extract_written_paths("dd of='out dir/out.ts'") == ["out dir/out.ts"]
