"""紧凑重定向回归测试（v1.0.1，P1 门禁绕过修复）。

背景：``shlex.split`` 默认只按空白切分，``echo x>fib.py`` 会把 ``x>fib.py``
当成一个 token，导致 ``extract_written_paths`` 漏检、阶段门禁可被「紧凑语法」
绕过——Agent 无需 ``write_file`` 即可把实现 / 测试代码落到工作区。这里锁定
修复后的提取行为，并做一次端到端验收。
"""
import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.interceptors import extract_written_paths, find_unresolved_redirects

# (命令, 期望提取到的写路径)
COMPACT_REDIRECT_CASES = [
    ("echo x > fib.py", ["fib.py"]),
    ("echo x>fib.py", ["fib.py"]),
    ("echo x >fib.py", ["fib.py"]),
    ("echo x> fib.py", ["fib.py"]),
    ("echo x>>fib.py", ["fib.py"]),
    ("echo x>'fib.py'", ["fib.py"]),
    ('echo x>"fib.py"', ["fib.py"]),
    ("echo x 1>fib.py", ["fib.py"]),
    ("echo x 2>>fib.py", ["fib.py"]),
    ("echo x &>fib.py", ["fib.py"]),
    ("echo x>|fib.py", ["fib.py"]),
    ("echo x 2>&1 > fib.py", ["fib.py"]),
    ("printf 'x'>out.ts", ["out.ts"]),
    ("tr a b<spec.md>fib.py", ["fib.py"]),
    ("echo a>tests/test_b.py", ["tests/test_b.py"]),
    ("if [ 1 -gt 0 ]; then echo x>fib.py; fi", ["fib.py"]),
    ("echo hi>notes.md", ["notes.md"]),
    ("echo x>y.py && echo z>>w.py", ["y.py", "w.py"]),
    ("echo x > y.py;echo z>>w.py", ["y.py", "w.py"]),
]

# 非重定向写法：``>`` 出现在引号 / 比较 / 箭头里，不应被当成写路径
NON_REDIRECT_CASES = [
    'echo "a>b"',
    "echo '1>2'",
    "echo a->b",
    "echo a=>b",
    "echo a>=b",
    "echo a!=b",
    "cmd 2>&1",
    "echo a\\>b",
    "ls -la",
    "cat spec.md",
]


@pytest.mark.parametrize("command,expected", COMPACT_REDIRECT_CASES)
def test_compact_redirection_extracted(command, expected):
    assert extract_written_paths(command) == expected


@pytest.mark.parametrize("command", NON_REDIRECT_CASES)
def test_non_redirect_forms_extract_nothing(command):
    assert extract_written_paths(command) == []


def test_spaced_forms_unchanged():
    assert extract_written_paths("echo hello > fib.py") == ["fib.py"]
    assert extract_written_paths("cmd >> test_fib.py") == ["test_fib.py"]
    assert extract_written_paths("tee logs.txt") == ["logs.txt"]
    assert extract_written_paths("cp src/a.py tests/b.py") == ["tests/b.py"]
    assert extract_written_paths("sed -i s/a/b/ fib.py") == ["fib.py"]
    assert extract_written_paths("touch spec.md") == ["spec.md"]
    assert extract_written_paths("") == []


def test_compact_redirect_blocked_end_to_end_at_stage1(tmp_path):
    """验收：阶段 1 无法用紧凑重定向写入实现代码，且非源码文件不受影响。"""
    skill = AntiShortcutSkill(tmp_path, user_request="实现一个计算斐波那契数列的函数")
    executed: list[str] = []

    def write_file(path, content):
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    def execute_command(command):
        executed.append(command)
        return {"exit_code": 0, "output": ""}

    tools = skill.install({"write_file": write_file, "execute_command": execute_command})
    assert skill.current_stage == 1

    tools["execute_command"]("echo hi>notes.md")  # 非源码 / 测试文件：仍放行
    assert executed == ["echo hi>notes.md"]

    with pytest.raises(PermissionError):
        tools["execute_command"]("echo def fib(): pass>fib.py")
    assert executed == ["echo hi>notes.md"]        # 被拒命令未下发
    assert not (tmp_path / "fib.py").exists()      # 工作区未被写入实现代码


# ---------- 纵深防御：重定向目标解析不出即拒绝（2-A 口径） ----------

@pytest.mark.parametrize(
    "command,expected",
    [
        ("echo payload >", [">"]),
        ("echo payload >|", [">|"]),
        ("echo payload > ; echo done", [">;"]),
        ("echo payload 2>>; echo done", ["2>>;"]),
    ],
)
def test_unresolved_redirect_detected(command, expected):
    assert find_unresolved_redirects(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "echo x>fib.py",              # 紧凑写法：目标可解析
        'echo x>"my file.py"',        # 带空格目标
        "cmd 2>&1",                   # fd 复制，不是文件写入
        "cmd >&2",
        "cmd > /dev/null",
        'echo "a>b"',                 # 引号内的 > 不算重定向
        "ls -la",
    ],
)
def test_resolved_or_non_write_redirects_not_flagged(command):
    assert find_unresolved_redirects(command) == []


def test_unresolved_redirect_blocked_end_to_end(tmp_path):
    """目标解析不出的重定向一律拒绝，且命令未下发。"""
    skill = AntiShortcutSkill(tmp_path, user_request="实现 fib")
    executed: list[str] = []

    def execute_command(command):
        executed.append(command)
        return {"exit_code": 0, "output": ""}

    tools = skill.install(
        {"write_file": lambda path, content: None, "execute_command": execute_command}
    )
    with pytest.raises(PermissionError):
        tools["execute_command"]("echo payload >|")
    assert executed == []
