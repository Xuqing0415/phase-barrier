"""pytest 共享 fixtures 与常量。"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# 沙箱兼容：本机沙箱对 ``mkdir(mode=0o700)`` 创建的目录会设置拒绝读取的 ACL
# （后续 os.scandir / iterdir 直接 PermissionError，且无法 chmod 补救）。
# pytest 默认以 0o700 创建临时目录，这里在测试会话内把该 mode 替换为 0o755。
_orig_mkdir = Path.mkdir


def _patched_mkdir(self, mode=0o777, *args, **kwargs):
    if mode == 0o700:
        mode = 0o755
    return _orig_mkdir(self, mode, *args, **kwargs)


Path.mkdir = _patched_mkdir

USER_REQUEST = "实现一个计算斐波那契数列的函数 fib(n)"

SPEC = """# 斐波那契函数 Spec

## 需求分析
需要一个函数 fib(n)，计算斐波那契数列第 n 项。F(0)=0, F(1)=1。

## 设计方案
采用迭代法，滚动维护前两项，时间复杂度 O(n)。

## 接口定义
def fib(n: int) -> int
"""

GOOD_TESTS = '''"""测试用例"""
import pytest
from fib import fib


def test_base_cases():
    assert fib(0) == 0
    assert fib(1) == 1


def test_known_value():
    assert fib(10) == 55


def test_rejects_negative():
    with pytest.raises(ValueError):
        fib(-1)
'''

EMPTY_TESTS = '''def test_nothing():
    pass
'''

GOOD_IMPL = '''def fib(n):
    if n < 0:
        raise ValueError("n must be >= 0")
    if n <= 1:
        return n
    a, b = 0, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return b
'''

BUGGY_IMPL = GOOD_IMPL.replace("return b", "return a  # bug")


@pytest.fixture
def isolated_workspace(tmp_path: Path) -> Path:
    """工作区：写入 pytest.ini 隔离 rootdir，避免上溯到仓库根 pyproject.toml。"""
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def fake_tools(tmp_path: Path) -> dict:
    """真实文件系统 + 真实 shell 的模拟 Agent 工具。"""

    def write_file(path, content):
        p = tmp_path / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(p)}

    def execute_command(command):
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=tmp_path,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return {"exit_code": proc.returncode, "output": output}

    return {"write_file": write_file, "execute_command": execute_command}
