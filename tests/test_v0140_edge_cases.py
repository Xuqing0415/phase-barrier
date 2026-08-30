"""v0.14.0 边界补强测试：脚本类写入检测 + verify/export-evidence CLI 错误处理。"""
import json
from pathlib import Path

import pytest

from anti_shortcut import AntiShortcutSkill
from anti_shortcut.__main__ import main
from anti_shortcut.evidence import EVIDENCE_MANIFEST_NAME
from anti_shortcut.interceptors import extract_written_paths
from conftest import SPEC, USER_REQUEST


def make_skill(tmp_path: Path, **kwargs) -> AntiShortcutSkill:
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = .\n", encoding="utf-8")
    return AntiShortcutSkill(tmp_path, user_request="实现 fib(n)", **kwargs)


# ---------- 脚本类写入提取（python -c / node -e / bash -c） ----------

def test_extract_script_write_python_open():
    assert extract_written_paths("python -c \"open('fib.py','w').write('x')\"") == ["fib.py"]
    assert extract_written_paths("python3 -c \"open('log.txt','a').write('x')\"") == ["log.txt"]
    assert extract_written_paths("python -c \"open('x','wb').write(b'1')\"") == ["x"]


def test_extract_script_write_pathlib_and_node():
    assert extract_written_paths(
        "python -c \"from pathlib import Path; Path('test_x.py').write_text('')\""
    ) == ["test_x.py"]
    assert extract_written_paths(
        "node -e \"require('fs').writeFileSync('out.ts','x')\""
    ) == ["out.ts"]
    assert extract_written_paths(
        "node -e \"fs.appendFile('src/a.js','x',()=>{})\""
    ) == ["src/a.js"]


def test_extract_script_write_shell_redirect():
    assert extract_written_paths("bash -c \"cat > out.txt\"") == ["out.txt"]
    assert extract_written_paths("sh -c 'echo hi'") == []
    assert extract_written_paths("curl -c cookies.txt") == []
    assert extract_written_paths("python fib.py") == []


def test_extract_script_read_only_not_extracted():
    assert extract_written_paths("python -c \"print(open('spec.md').read())\"") == []
    assert extract_written_paths("python -c \"open('x','r')\"") == []
    assert extract_written_paths("node -e \"console.log('hi')\"") == []


# ---------- Skill 层：脚本写入受阶段门禁约束 ----------

def test_exec_python_c_write_source_blocked(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="实现代码"):
        tools["execute_command"]("python -c \"open('fib.py','w').write('x')\"")


def test_exec_node_write_source_blocked(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="实现代码"):
        tools["execute_command"]("node -e \"require('fs').writeFileSync('fib.py','x')\"")


def test_exec_bash_redirect_source_blocked(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="实现代码"):
        tools["execute_command"]("bash -c \"cat > fib.py\"")


def test_exec_script_read_only_allowed(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    r = tools["execute_command"]("python -c \"print('hi')\"")
    assert r["exit_code"] == 0


def test_exec_script_write_source_allowed_after_impl(tmp_path, fake_tools):
    skill = make_skill(tmp_path)
    tools = skill.install(fake_tools)
    skill.state._data["current_stage"] = 3  # 模拟已完成实现阶段
    r = tools["execute_command"]("python -c \"open('fib.py','w').write('x')\"")
    assert r["exit_code"] == 0
    assert (tmp_path / "fib.py").exists()


def test_exec_script_write_js_source_blocked(tmp_path, fake_tools):
    """JS 适配器下，脚本写入 .js 源文件同样受阶段门禁约束。"""
    skill = make_skill(tmp_path, config={"language": "javascript"})
    tools = skill.install(fake_tools)
    with pytest.raises(PermissionError, match="实现代码"):
        tools["execute_command"]("node -e \"require('fs').writeFileSync('fib.js','x')\"")


# ---------- CLI：verify-evidence / export-evidence 错误处理 ----------

def _make_spec_evidence(tmp_path: Path) -> None:
    (tmp_path / "spec.md").write_text(SPEC, encoding="utf-8")
    skill = AntiShortcutSkill(tmp_path, user_request=USER_REQUEST)
    assert skill.advance_stage(2)["success"]


def test_cli_verify_evidence_corrupt_manifest(capsys, tmp_path):
    gate = tmp_path / ".agent_gate"
    gate.mkdir()
    (gate / EVIDENCE_MANIFEST_NAME).write_text("{broken", encoding="utf-8")
    rc = main(["verify-evidence", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "清单" in err and "Traceback" not in err


def test_cli_verify_evidence_bad_version(capsys, tmp_path):
    gate = tmp_path / ".agent_gate"
    gate.mkdir()
    (gate / EVIDENCE_MANIFEST_NAME).write_text('{"version": 999, "entries": {}}', encoding="utf-8")
    rc = main(["verify-evidence", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "版本" in err and "Traceback" not in err


def test_cli_verify_evidence_unsigned_manifest_with_key(capsys, tmp_path):
    """配置了 HMAC 密钥但清单未签名：视为可疑，友好报错。"""
    _make_spec_evidence(tmp_path)
    cfg = tmp_path / "gate.yaml"
    cfg.write_text("state_hmac_key: k\n", encoding="utf-8")
    rc = main(["verify-evidence", "--workspace", str(tmp_path), "--config", str(cfg)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "签名" in err and "Traceback" not in err


def test_cli_verify_evidence_missing_workspace(capsys, tmp_path):
    rc = main(["verify-evidence", "--workspace", str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "工作区" in err


def test_cli_export_evidence_corrupt_manifest(capsys, tmp_path):
    gate = tmp_path / ".agent_gate"
    gate.mkdir()
    (gate / EVIDENCE_MANIFEST_NAME).write_text("not json", encoding="utf-8")
    rc = main(["export-evidence", "--workspace", str(tmp_path)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "清单" in err and "Traceback" not in err


def test_cli_export_evidence_nested_out(capsys, tmp_path):
    """--out 指向不存在的子目录时自动创建父目录。"""
    _make_spec_evidence(tmp_path)
    out_path = tmp_path / "sub" / "dir" / "bundle.json"
    rc = main(["export-evidence", "--workspace", str(tmp_path), "--out", str(out_path)])
    out = capsys.readouterr().out
    assert rc == 0 and "已导出" in out
    assert out_path.exists()
    bundle = json.loads(out_path.read_text(encoding="utf-8"))
    assert bundle["verified"] is True


def test_cli_export_evidence_missing_workspace(capsys, tmp_path):
    rc = main(["export-evidence", "--workspace", str(tmp_path / "nope")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR" in err and "工作区" in err
