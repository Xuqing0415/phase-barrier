"""阶段 2 校验范围与语言消歧（v0.62.0）——SWE-bench 存量仓库实证回归。

来源（Scale-20 容器实测，6 个 gated 实例卡死在阶段 2）：

- django / sphinx 仓库根目录带 ``package.json``（前端 / 文档工具链），
  ``detect_language`` 按标志文件顺序误判为 javascript，随后用 JS 启发式
  校验 ``.py`` 文件 → 「断言关键字不足（0 < 1）」，阶段 2 永远无法推进；
- 即使语言判定正确，全量扫描几十年存量的测试目录也会被夹具（sphinx 的
  ``tests/roots/.../dummy/test_nested.py``）和历史空壳测试卡住；
- 反过来，全量扫描还允许 Agent 不写新测试，直接拿仓库已有测试过关。
"""
import subprocess
from pathlib import Path

from anti_shortcut import load_config
from anti_shortcut.languages import detect_language
from anti_shortcut.state import StateManager
from anti_shortcut.validators import validate_tests


def git(ws: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ws), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def init_repo(ws: Path) -> Path:
    ws.mkdir(parents=True, exist_ok=True)
    assert git(ws, "init", "-q", "-b", "main").returncode == 0
    git(ws, "config", "user.email", "test@example.com")
    git(ws, "config", "user.name", "Test")
    return ws


def commit(ws: Path, message: str = "base") -> None:
    git(ws, "add", "-A")
    assert git(ws, "commit", "-q", "-m", message).returncode == 0


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


REPO_TESTS = (
    "def test_existing_one():\n    assert 1 == 1\n\n"
    "def test_existing_two():\n    assert 2 == 2\n"
)
NEW_TESTS = "def test_new_behaviour():\n    assert True\n"


def cfg(**overrides):
    base = {"test_file_patterns": ["test_*.py"], "min_test_functions": 1}
    base.update(overrides)
    return load_config(base)


def state_of(ws: Path) -> StateManager:
    return StateManager(ws / ".agent_gate" / "state.json")


def posix(files: list[str]) -> list[str]:
    """证据里的路径按平台分隔符记录，断言时统一成正斜杠。"""
    return [f.replace("\\", "/") for f in files]


# ---------- 语言消歧 ----------

def test_detect_language_prefers_dominant_python_over_package_json(tmp_path):
    """django 布局：package.json + setup.py + 大量 .py -> python（不是 javascript）。"""
    ws = tmp_path / "django-like"
    write(ws / "package.json", "{}\n")
    write(ws / "setup.py", "from setuptools import setup\nsetup()\n")
    for i in range(6):
        write(ws / "django" / f"mod{i}.py", "x = 1\n")
    write(ws / "js_tests" / "admin" / "jsi18n-mocks.test.js", "// nothing here\n")
    assert detect_language(ws) == "python"


def test_detect_language_keeps_javascript_for_node_repo(tmp_path):
    """Node 仓库带少量 .py 工具脚本时仍应判为 javascript。"""
    ws = tmp_path / "node-like"
    write(ws / "package.json", "{}\n")
    write(ws / "pyproject.toml", "[tool.black]\n")
    for i in range(5):
        write(ws / "src" / f"mod{i}.ts", "export const x = 1;\n")
    write(ws / "scripts" / "helper.py", "print(1)\n")
    assert detect_language(ws) == "javascript"


def test_detect_language_ignores_dependency_dirs(tmp_path):
    """node_modules 里的 JS 不能把 Python 仓库拉向 javascript。"""
    ws = tmp_path / "py-with-node-modules"
    write(ws / "package.json", "{}\n")
    write(ws / "setup.py", "from setuptools import setup\nsetup()\n")
    for i in range(3):
        write(ws / "pkg" / f"mod{i}.py", "x = 1\n")
    for i in range(20):
        write(ws / "node_modules" / "dep" / f"f{i}.js", "module.exports = 1;\n")
    assert detect_language(ws) == "python"


def test_detect_language_single_marker_unchanged(tmp_path):
    """只有一个标志文件（空目录）时保持既有优先级。"""
    ws = tmp_path / "only-js"
    write(ws / "package.json", "{}\n")
    assert detect_language(ws) == "javascript"
    ws2 = tmp_path / "only-py"
    write(ws2 / "requirements.txt", "pytest\n")
    assert detect_language(ws2) == "python"


# ---------- 阶段 2 校验范围 ----------

def test_stage2_scope_requires_changed_test_file(tmp_path):
    """只改实现不写测试：不能用仓库已有的测试过关。"""
    ws = init_repo(tmp_path / "repo")
    write(ws / "test_repo.py", REPO_TESTS)
    write(ws / "fib.py", "def fib(n):\n    return n\n")
    commit(ws)

    write(ws / "fib.py", "def fib(n):\n    return n + 1\n")
    ok, msg, _ = validate_tests(ws, cfg(), state_of(ws))
    assert not ok and "本次变更中没有测试文件" in msg

    write(ws / "test_fib_new.py", NEW_TESTS)
    ok, msg, ev = validate_tests(ws, cfg(), state_of(ws))
    assert ok, msg
    assert ev["files"] == ["test_fib_new.py"]
    assert ev["test_count"] == 1


def test_stage2_scope_lenient_for_test_dir_convention(tmp_path):
    """django 风格：测试文件叫 tests.py（不匹配 test_*.py），位于 tests/ 下。"""
    ws = init_repo(tmp_path / "repo")
    write(ws / "pkg" / "__init__.py", "")
    commit(ws)
    write(
        ws / "tests" / "app" / "tests.py",
        "class MyTests:\n    def test_something(self):\n        assert 1 == 1\n",
    )
    ok, msg, ev = validate_tests(ws, cfg(), state_of(ws))
    assert ok, msg
    assert posix(ev["files"]) == ["tests/app/tests.py"]


def test_stage2_scope_clean_repo_falls_back_to_workspace_scan(tmp_path):
    """仓库干净（git 无变更）时退回全量扫描，兼容已提交的工作流。"""
    ws = init_repo(tmp_path / "repo")
    write(ws / "test_repo.py", REPO_TESTS)
    commit(ws)
    ok, msg, ev = validate_tests(ws, cfg(), state_of(ws))
    assert ok, msg
    assert "test_repo.py" in ev["files"]


def test_stage2_scope_workspace_mode_scans_whole_workspace(tmp_path):
    """显式 ``stage2_test_scope: workspace`` 保留旧行为。"""
    ws = init_repo(tmp_path / "repo")
    write(ws / "test_repo.py", REPO_TESTS)
    write(ws / "fib.py", "def fib(n):\n    return n\n")
    commit(ws)
    write(ws / "fib.py", "def fib(n):\n    return n + 1\n")
    ok, msg, _ = validate_tests(ws, cfg(stage2_test_scope="workspace"), state_of(ws))
    assert ok, msg


def test_stage2_scope_syntax_error_in_changed_test_still_rejected(tmp_path):
    ws = init_repo(tmp_path / "repo")
    write(ws / "keep.py", "x = 1\n")
    commit(ws)
    write(ws / "test_broken.py", "def test_x(:\n")
    ok, msg, _ = validate_tests(ws, cfg(), state_of(ws))
    assert not ok and "语法错误" in msg


def test_stage2_scope_lenient_skips_non_test_files_in_test_dir(tmp_path):
    """tests/ 下的非测试文件（工具模块）：宽松模式不应据此放行或硬报语法错。"""
    ws = init_repo(tmp_path / "repo")
    write(ws / "keep.py", "x = 1\n")
    commit(ws)
    write(ws / "tests" / "helper_util.py", "def helper():\n    return 1\n")
    ok, msg, _ = validate_tests(ws, cfg(), state_of(ws))
    assert not ok and "没有可识别的测试用例" in msg


def test_stage2_scope_lenient_ignores_syntax_error_in_test_dir_helper(tmp_path):
    """tests/ 下的辅助脚本语法错误，不应被当成「测试文件语法错误」拦截。"""
    ws = init_repo(tmp_path / "repo")
    write(ws / "keep.py", "x = 1\n")
    commit(ws)
    write(ws / "tests" / "raw_fixture.py", "def broken(:\n")
    ok, msg, _ = validate_tests(ws, cfg(), state_of(ws))
    assert not ok and "没有可识别的测试用例" in msg


# ---------- 端到端布局回归（原假阳性） ----------

def test_django_layout_no_longer_blocks_stage2(tmp_path):
    """django 布局 + 本次变更的测试文件：阶段 2 必须放行。"""
    ws = init_repo(tmp_path / "django")
    write(ws / "package.json", "{}\n")
    write(ws / "setup.py", "from setuptools import setup\nsetup()\n")
    for i in range(6):
        write(ws / "django" / f"mod{i}.py", "x = 1\n")
    write(ws / "js_tests" / "admin" / "jsi18n-mocks.test.js", "// nothing here\n")
    commit(ws)

    write(ws / "django" / "mod0.py", "x = 2\n")
    write(ws / "test_pb_repro.py", NEW_TESTS)
    ok, msg, ev = validate_tests(ws, cfg(min_test_functions=1), state_of(ws))
    assert ok, msg
    assert ev["files"] == ["test_pb_repro.py"]


def test_sphinx_layout_fixture_does_not_block_stage2(tmp_path):
    """sphinx 布局：存量夹具 .py（0 断言）+ 本次变更的测试文件 -> 放行。"""
    ws = init_repo(tmp_path / "sphinx")
    write(ws / "package.json", "{}\n")
    write(ws / "pyproject.toml", "[build-system]\n")
    write(ws / "sphinx" / "__init__.py", "")
    write(ws / "tests" / "roots" / "test-inheritance" / "dummy" / "test_nested.py", "")
    commit(ws)

    assert detect_language(ws) == "python"
    write(ws / "sphinx" / "__init__.py", "__version__ = '1'\n")
    write(ws / "test_pb_repro.py", NEW_TESTS)
    ok, msg, ev = validate_tests(ws, cfg(min_test_functions=1), state_of(ws))
    assert ok, msg
    assert ev["files"] == ["test_pb_repro.py"]
