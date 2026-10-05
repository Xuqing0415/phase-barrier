"""pytest 共享 fixtures 与常量。"""
from __future__ import annotations

import os
import subprocess
import tempfile
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

# ``tempfile.mkdtemp`` 直接走 ``os.mkdir(..., 0o700)``，不经过 ``Path.mkdir``：
# pytest 的 ``pytest-cache-files-*`` 由它创建，在本机同样会被 ACL 拒绝，
# 导致 basetemp 清理时报 WinError 5（v1.0.1）。仅在 Windows 上放宽该 mode。
if os.name == "nt":
    _orig_os_mkdir = os.mkdir

    def _patched_os_mkdir(path, mode=0o777, *args, **kwargs):
        if mode == 0o700:
            mode = 0o755
        return _orig_os_mkdir(path, mode, *args, **kwargs)

    os.mkdir = _patched_os_mkdir


def _ensure_usable_pytest_temproot() -> None:
    """默认临时根不可用时，把 pytest 的 temproot 切到工作区内（v1.0.1）。

    ``%TEMP%\\pytest-of-<user>`` 一旦被历史遗留的拒绝 ACL 污染（WinError 5），
    pytest 首次使用 ``tmp_path`` 就会抛 ``PermissionError``，大批用例在 setup
    阶段直接 error。这里预检默认根目录能否列目录，不可用则改用
    ``PYTEST_DEBUG_TEMPROOT``——pytest 直到 ``getbasetemp()`` 才读取它，
    所以在 conftest 导入期设置依然生效。
    """
    if os.environ.get("PYTEST_DEBUG_TEMPROOT"):
        return
    import getpass

    default_root = Path(tempfile.gettempdir()) / f"pytest-of-{getpass.getuser()}"
    try:
        default_root.mkdir(parents=True, exist_ok=True)
        os.listdir(default_root)
    except OSError:
        fallback = Path(__file__).resolve().parents[1] / ".pytest_tmp" / "tmp-root"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover（兜底目录也不可写时保持原行为）
            return
        os.environ["PYTEST_DEBUG_TEMPROOT"] = str(fallback)


_ensure_usable_pytest_temproot()

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
