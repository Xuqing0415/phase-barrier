"""纵深防御 2-B（v1.0.1，检测版）：执行前后工作区快照 + 未授权写入审计。

覆盖：
- ``snapshot_workspace`` / ``diff_snapshots`` 的纯函数语义（新增 / 修改 / 删除 + 排除项）；
- ``execute_command`` 里「没经过 write_file 记录」的写入会被标记成未授权写入
  （检测 + 审计日志 + 状态证据）；
- ``write_file`` 记录的写入不会被误报；``write_audit: false`` 可整体关闭。
"""
from __future__ import annotations

from pathlib import Path

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.write_audit import (
    diff_snapshots,
    iter_workspace_files,
    snapshot_workspace,
)


def _make_skill(tmp_path: Path, **audit_opts) -> AntiShortcutSkill:
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    return AntiShortcutSkill(
        tmp_path,
        config={"defense": {"behavior_audit": {"enabled": True, **audit_opts}}},
        user_request="实现数据清理工具",
    )


# ---------- 纯函数：快照与差异 ----------

def test_diff_snapshots_detects_created_modified_deleted():
    before = {"a.py": (1, 10), "b.py": (2, 20)}
    after = {"a.py": (1, 10), "b.py": (3, 25), "c.py": (4, 30)}
    changes = {(c.path, c.change) for c in diff_snapshots(before, after)}
    assert changes == {("b.py", "modified"), ("c.py", "created")}
    # 删除：文件从快照里消失
    assert {(c.path, c.change) for c in diff_snapshots(after, before)} == {
        ("b.py", "modified"),
        ("c.py", "deleted"),
    }


def test_snapshot_skips_excluded_dirs_files_and_suffixes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    for d in ("__pycache__", ".git", ".agent_gate", "node_modules", ".pytest_cache"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "noise.txt").write_text("noise", encoding="utf-8")
    (tmp_path / "pytest-cache-files-abc").mkdir()
    (tmp_path / "pytest-cache-files-abc" / "noise.txt").write_text("noise", encoding="utf-8")
    (tmp_path / "mod.pyc").write_bytes(b"\x00")
    (tmp_path / ".coverage").write_text("", encoding="utf-8")
    (tmp_path / "coverage.xml").write_text("<xml/>", encoding="utf-8")

    snap = snapshot_workspace(tmp_path)
    assert set(snap) == {"src/main.py", "pytest.ini"} or set(snap) == {"src/main.py"}
    assert "src/main.py" in snap


def test_iter_workspace_files_returns_posix_relative_paths(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("a", encoding="utf-8")
    rels = {rel for rel, _ in iter_workspace_files(tmp_path)}
    assert "pkg/a.py" in rels


# ---------- 集成：exec 写入被标记 ----------

def test_exec_created_file_is_flagged_as_unauthorized(tmp_path, fake_tools):
    skill = _make_skill(tmp_path)
    tools = skill.install(fake_tools)
    # `other` 文件（非源码 / 非测试）在任何阶段都允许写入 -> 门禁放行，但审计必须标记
    tools["execute_command"]("echo hi > sneak.txt")

    assert (tmp_path / "sneak.txt").exists()
    writes = skill.unauthorized_writes
    assert [w["path"] for w in writes] == ["sneak.txt"]
    assert writes[0]["change"] == "created"
    assert "sneak.txt" in writes[0]["command"]

    evidence = skill.state.get_evidence("unauthorized_writes") or []
    assert [w["path"] for w in evidence] == ["sneak.txt"]
    log = (tmp_path / ".agent_gate" / "audit.log").read_text(encoding="utf-8")
    assert "unauthorized_write" in log


def test_exec_deleted_file_is_flagged_as_unauthorized_delete(tmp_path, fake_tools):
    skill = _make_skill(tmp_path)
    tools = skill.install(fake_tools)
    (tmp_path / "victim.txt").write_text("bye", encoding="utf-8")
    for command in ("del victim.txt", "rm -f victim.txt"):
        try:
            tools["execute_command"](command)
        except PermissionError:
            continue
        if not (tmp_path / "victim.txt").exists():
            break
    assert not (tmp_path / "victim.txt").exists(), "两个平台都没删掉文件，用例前提不成立"
    deletes = [w for w in skill.unauthorized_writes if w["change"] == "deleted"]
    assert [w["path"] for w in deletes] == ["victim.txt"]


def test_write_file_recorded_changes_are_not_flagged(tmp_path, fake_tools):
    skill = _make_skill(tmp_path)
    tools = skill.install(fake_tools)
    tools["write_file"]("notes.md", "hello")
    tools["execute_command"]("echo more >> notes.md")
    assert (tmp_path / "notes.md").read_text(encoding="utf-8").startswith("hello")
    assert skill.unauthorized_writes == []


def test_write_audit_can_be_disabled(tmp_path, fake_tools):
    skill = _make_skill(tmp_path, write_audit=False)
    tools = skill.install(fake_tools)
    tools["execute_command"]("echo hi > sneak.txt")
    assert (tmp_path / "sneak.txt").exists()
    assert skill.unauthorized_writes == []
    assert skill.state.get_evidence("unauthorized_writes") is None


def test_write_audit_off_when_behavior_audit_disabled(tmp_path, fake_tools):
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    skill = AntiShortcutSkill(tmp_path, user_request="需求")  # 防线 4 默认关闭
    tools = skill.install(fake_tools)
    tools["execute_command"]("echo hi > sneak.txt")
    assert skill.unauthorized_writes == []