"""集成层测试：bootstrap / 插件注册与加载 / 入口点发现。"""
from __future__ import annotations

import pytest

from anti_shortcut import AntiShortcutSkill, bootstrap, install_into, load_plugins, register_integration
import anti_shortcut.integration as _integration
from conftest import GOOD_IMPL, GOOD_TESTS, SPEC, USER_REQUEST


@pytest.fixture(autouse=True)
def _clean_registry():
    """插件注册表为进程级全局状态，测试间必须清理，避免互相污染。"""
    saved = dict(_integration._registry)
    _integration._registry.clear()
    yield
    _integration._registry.clear()
    _integration._registry.update(saved)


def _empty_tools():
    return {"write_file": lambda *a, **k: None, "execute_command": lambda *a, **k: None}


def test_bootstrap_wraps_tools(tmp_path, fake_tools):
    orig_write = fake_tools["write_file"]
    orig_exec = fake_tools["execute_command"]
    skill = bootstrap(fake_tools, tmp_path, user_request=USER_REQUEST)
    assert isinstance(skill, AntiShortcutSkill)
    assert "advance_stage" in fake_tools
    assert fake_tools["write_file"] is not orig_write
    assert fake_tools["execute_command"] is not orig_exec


def test_install_into_returns_tools(tmp_path):
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    tools = _empty_tools()
    result = install_into(tools, skill)
    assert result is tools
    assert "advance_stage" in tools


def test_register_and_load_plugins(tmp_path):
    calls = []

    def installer(agent, skill):
        calls.append((agent, skill))

    register_integration("test-plugin", installer)
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    loaded = load_plugins(agent="fake-agent", skill=skill)
    assert "test-plugin" in loaded
    assert calls == [("fake-agent", skill)]


def test_load_plugins_installs_wrapped_tools(tmp_path):
    """接口约定：插件负责把包装后的工具装回 agent。"""

    def installer(agent, skill):
        skill.install(agent["tools"])

    register_integration("tool-installer", installer)
    agent = {"tools": _empty_tools()}
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    load_plugins(agent=agent, skill=skill)
    assert "advance_stage" in agent["tools"]


def test_bootstrap_full_flow_via_tools(tmp_path, fake_tools):
    """通过 bootstrap 全流程：完成交付。"""
    skill = bootstrap(fake_tools, tmp_path, user_request=USER_REQUEST)
    tools = fake_tools

    tools["write_file"]("spec.md", SPEC)
    assert tools["advance_stage"](2)["success"]
    tools["write_file"]("test_fib.py", GOOD_TESTS)
    assert tools["advance_stage"](3)["success"]
    tools["write_file"]("fib.py", GOOD_IMPL)
    assert tools["advance_stage"](4)["success"]
    r = tools["execute_command"]("python -m pytest test_fib.py -q -p no:cacheprovider")
    assert r["exit_code"] == 0
    assert tools["advance_stage"](5)["success"]
    assert skill.is_complete
