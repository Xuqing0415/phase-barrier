"""verify-evidence --git-base Git 门禁测试（v0.11.0）。"""
import json
import subprocess
import sys
from pathlib import Path

from anti_shortcut.__main__ import main
from conftest import SPEC

REPO_ROOT = Path(__file__).resolve().parents[1]


def git(ws, *args):
    return subprocess.run(
        ["git", "-C", str(ws), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def init_repo(ws: Path) -> None:
    git(ws, "init", "-q", "-b", "main")
    git(ws, "config", "user.email", "test@example.com")
    git(ws, "config", "user.name", "Test")


def make_spec_repo(tmp_path) -> Path:
    """仓库：提交 1 = spec.md + 证据清单（阶段推进到 2）。

    用子进程跑 ``advance``：进程退出时审计日志文件句柄必然落盘，
    避免测试进程内 structlog 全局缓存导致 audit.log 延迟 flush、被后续提交带走。
    """
    ws = tmp_path / "repo"
    ws.mkdir()
    init_repo(ws)
    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "anti_shortcut", "advance", "--workspace", str(ws), "--to", "2"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "base: spec")
    return ws


def test_git_gate_detects_evidence_change(capsys, tmp_path):
    ws = make_spec_repo(tmp_path)
    (ws / "spec.md").write_text(SPEC + "\n# changed\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "tamper spec")
    rc = main(["verify-evidence", "--workspace", str(ws), "--git-base", "HEAD~1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "证据文件在本次变更中被修改" in err


def test_git_gate_passes_when_unrelated_file_changed(capsys, tmp_path):
    ws = make_spec_repo(tmp_path)
    (ws / "README.md").write_text("docs\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "docs")
    rc = main(["verify-evidence", "--workspace", str(ws), "--git-base", "HEAD~1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OK" in out


def test_git_gate_bad_base_fails(capsys, tmp_path):
    ws = make_spec_repo(tmp_path)
    rc = main(["verify-evidence", "--workspace", str(ws), "--git-base", "no-such-ref"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "ERROR" in err and "git" in err.lower()


def test_git_gate_json_includes_changed_files(capsys, tmp_path):
    ws = make_spec_repo(tmp_path)
    (ws / "README.md").write_text("docs\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "docs")
    rc = main(["verify-evidence", "--workspace", str(ws), "--git-base", "HEAD~1", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["git_base"] == "HEAD~1"
    assert "README.md" in payload["git_changed_files"]
    assert payload["ok"] is True



# ---------- v0.26.0：变更文件 -> 门禁影响映射 ----------

def test_git_gate_impact_classifies_source_change(capsys, tmp_path):
    ws = make_spec_repo(tmp_path)
    (ws / "fib.py").write_text("def fib(n): return n\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "add impl")
    rc = main(["verify-evidence", "--workspace", str(ws), "--git-base", "HEAD~1", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    impact = {item["file"]: item for item in payload["git_impact"]}
    assert "fib.py" in impact
    assert impact["fib.py"]["kind"] == "source"
    assert "重新运行测试" in impact["fib.py"]["requires"]


def test_git_gate_impact_classifies_spec_change(capsys, tmp_path):
    ws = make_spec_repo(tmp_path)
    (ws / "spec.md").write_text(SPEC + "\n# 补充验收标准\n", encoding="utf-8")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "update spec")
    # spec 属于证据清单条目 -> 应同时判定“证据文件被修改”（门禁失败）
    rc = main(["verify-evidence", "--workspace", str(ws), "--git-base", "HEAD~1", "--json"])
    out = capsys.readouterr().out
    assert rc == 1
    payload = json.loads(out)
    impact = {item["file"]: item for item in payload["git_impact"]}
    assert impact["spec.md"]["kind"] == "spec"
    assert "阶段 1" in impact["spec.md"]["requires"]
    assert any("证据文件在本次变更中被修改" in v for v in payload["violations"])
