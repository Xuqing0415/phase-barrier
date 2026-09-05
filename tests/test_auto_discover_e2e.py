"""自动发现端到端测试（v0.48.0）：真实 git 仓库 + 本地 fixture 插件。

模拟第三方插件仓库 ``tests/fixtures/plugin_alpha``（真实 git init/commit/
clone/ls-remote），只 mock 三个网络 / 工具边界：

- ``github_search``：GitHub Search API -> 本地 fixture 仓库；
- ``_entry_points_of_workdir``：pip 安装后 direct_url 归属（v0.46.1 已单测）;
- ``_run`` 中 ``pip install`` / ``plugin-verify`` 两个子进程（离线环境无法
  真正 pip install，CI 中 plugin-verify 已有独立自测 job 覆盖）；
- ``git clone`` / ``git ls-remote`` 远程传输：走本地等价实现（``copytree`` /
  ``rev-parse``）。真实 git 本地操作（init / add / commit / rev-parse）全部执行；
  Windows 沙箱禁止 Git for Windows 派生 ``sh`` 传输助手，故 clone / ls-remote
  无法真正 spawn；等价实现读取同一仓库 HEAD，判定语义与真实远端一致。

其余（discover 写回、增量刷新判定、失败重试、状态页同步）均真实执行。
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "auto_discover_plugins.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "plugin_alpha"
GROUP_KEYS = [
    "phase_barrier.languages",
    "phase_barrier.validators",
    "phase_barrier.interceptors",
    "anti_shortcut.integrations",
]


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_discover_plugins_e2e", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ad = _load_module()


_SECTION_RE = re.compile(r'^\[project\.entry-points\.("(?:[^"]+)")\]\s*$')
_ENTRY_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"')


def fixture_groups() -> dict[str, list[str]]:
    """从 fixture pyproject.toml 解析入口点组（正则实现，兼容 Python 3.10）。"""
    text = (FIXTURE / "pyproject.toml").read_text(encoding="utf-8")
    groups: dict[str, list[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        section = _SECTION_RE.match(line)
        if section:
            current = section.group(1)[1:-1]
            continue
        if line.startswith("[") and line.endswith("]"):
            current = None
            continue
        entry = _ENTRY_RE.match(line) if current is not None else None
        if entry and current in GROUP_KEYS:
            groups.setdefault(current, []).append(entry.group(1))
    return {key: sorted(names) for key, names in groups.items()}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def make_source_repo(tmp_path: Path, extra_commit: bool = False) -> tuple[Path, str, str | None]:
    """把 fixture 复制成真实 git 仓库，返回 (repo, sha1, sha2|None)。"""
    src = tmp_path / "plugin_alpha_src"
    shutil.copytree(FIXTURE, src)
    _git(src, "init", "-q", "-b", "main")
    _git(src, "add", "-A")
    _git(src, "-c", "user.name=tester", "-c", "user.email=tester@example.com", "commit", "-q", "-m", "init plugin")
    sha1 = _git(src, "rev-parse", "HEAD").stdout.strip()
    sha2 = None
    if extra_commit:
        (src / "feature.md").write_text("# feature\n", encoding="utf-8")
        _git(src, "add", "-A")
        _git(src, "-c", "user.name=tester", "-c", "user.email=tester@example.com", "commit", "-q", "-m", "add feature")
        sha2 = _git(src, "rev-parse", "HEAD").stdout.strip()
    return src, sha1, sha2


def repo_dict(src: Path) -> dict:
    return {
        "full_name": "acme/plugin-alpha",
        "html_url": "https://github.com/acme/plugin-alpha",
        "clone_url": src.as_uri(),
        "fork": False,
        "archived": False,
    }


def ok_payload(broken: list[str] | None = None) -> str:
    broken = broken or []
    groups = fixture_groups()
    plugins: dict[str, dict] = {}
    discovered: dict[str, list[dict]] = {}
    for group, names in groups.items():
        plugins[group] = {}
        discovered[group] = []
        for name in names:
            key = f"{group}:{name}"
            plugins[group][name] = {"ok": key not in broken, "errors": [] if key not in broken else ["boom"]}
            discovered[group].append({"name": name, "value": "x"})
    return json.dumps({"ok": not broken, "plugins": plugins, "discovered": discovered}, ensure_ascii=False)


class FakeTempDir:
    """每次调用返回工作区下的唯一临时目录（绕开 Windows 沙箱系统 %TEMP% ACL）。"""

    def __init__(self, base: Path):
        self.base = Path(base)
        self.n = 0

    def __call__(self, prefix="", **kwargs):
        self.n += 1
        path = self.base / f"{prefix or 'pb-'}{self.n}-{uuid.uuid4().hex[:10]}"
        path.mkdir(parents=True, exist_ok=False)
        return _TempCtx(str(path))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _TempCtx:
    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        return self.name

    def __exit__(self, *exc):
        return False


def install_env(
    monkeypatch,
    tmp_path: Path,
    src: Path,
    broken: list[str] | None = None,
    github_returns: list[dict] | None = None,
) -> list[list[dict]]:
    """布置端到端环境：mock GitHub 返回、入口点归属、pip/plugin-verify 子进程。"""
    orig_run = ad._run
    payload = ok_payload(broken)

    def _url_to_path(url: str) -> Path:
        return Path(url2pathname(urlparse(url).path))

    def router(cmd, **kwargs):
        joined = " ".join(cmd)
        if joined.startswith("git ls-remote"):
            # 本地等价：读取源仓库真实 HEAD（沙箱禁止 spawn git 传输助手）
            src = _url_to_path(cmd[2])
            sha = _git(src, "rev-parse", "HEAD").stdout.strip()
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{sha}\tHEAD", stderr="")
        if joined.startswith("git clone"):
            # 本地等价：复制含 .git 的源仓库，使后续 rev-parse 读取真实 SHA
            src = _url_to_path(cmd[5])  # git clone --quiet --depth 1 <url> <dst>
            dst = Path(cmd[-1])
            shutil.copytree(src, dst)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if "pip install" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout="installed", stderr="")
        if "plugin-verify" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout=payload, stderr="")
        return orig_run(cmd, **kwargs)

    monkeypatch.setattr(ad.tempfile, "TemporaryDirectory", FakeTempDir(tmp_path / "pbwork"))
    monkeypatch.setattr(ad, "github_search", lambda *a, **k: github_returns if github_returns is not None else [repo_dict(src)])
    monkeypatch.setattr(ad, "_entry_points_of_workdir", lambda workdir, dists=None: fixture_groups())
    monkeypatch.setattr(ad, "_run", router)
    synced: list[list[dict]] = []
    monkeypatch.setattr(ad.verify_plugins, "sync_index_docs", lambda index: synced.append(index))
    return synced


def index_with_auto_entry(tmp_path: Path, src: Path, sha: str) -> Path:
    container = {
        "plugins": [
            {
                "name": "phase-barrier-foo-adapter",
                "repo": "./examples/custom_adapter",
                "install": "./examples/custom_adapter",
                "entry_points": {"phase_barrier.languages": ["foo"]},
                "last_verified": "2026-09-05T00:00:00Z",
                "status": "passed",
                "auto_discovered": False,
                "last_commit_sha": None,
            },
            {
                "name": "acme/plugin-alpha",
                "repo": "https://github.com/acme/plugin-alpha",
                "install": f"git+{src}#egg=plugin-alpha",
                "entry_points": fixture_groups(),
                "last_verified": "2026-09-05T00:00:00Z",
                "status": "passed",
                "auto_discovered": True,
                "last_commit_sha": sha,
            },
        ],
        "auto_discovery": {"github_topic": "phase-barrier-plugin", "enabled": True},
    }
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps(container, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def empty_index(tmp_path: Path) -> Path:
    container = {
        "plugins": [
            {
                "name": "phase-barrier-foo-adapter",
                "repo": "./examples/custom_adapter",
                "install": "./examples/custom_adapter",
                "entry_points": {"phase_barrier.languages": ["foo"]},
                "last_verified": "2026-09-05T00:00:00Z",
                "status": "passed",
                "auto_discovered": False,
                "last_commit_sha": None,
            }
        ],
        "auto_discovery": {"github_topic": "phase-barrier-plugin", "enabled": True},
    }
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps(container, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class TestDiscoverE2E:
    def test_discovery_adds_fixture_plugin_with_real_sha(self, tmp_path, monkeypatch):
        src, sha1, _ = make_source_repo(tmp_path)
        index_path = empty_index(tmp_path)
        synced = install_env(monkeypatch, tmp_path, src)
        summary = ad.discover(index_path, dry_run=False)
        assert summary["added"] == ["acme/plugin-alpha"]
        assert summary["candidates"] == 1 and summary["failures"] == []
        assert summary["index_updated"] is True and summary["docs_synced"] is True
        assert len(synced) == 1
        written = json.loads(index_path.read_text(encoding="utf-8"))
        entry = next(e for e in written["plugins"] if e["name"] == "acme/plugin-alpha")
        assert entry["auto_discovered"] is True
        assert entry["status"] == "passed"
        assert entry["last_commit_sha"] == sha1
        assert len(entry["last_commit_sha"]) == 40
        assert entry["entry_points"] == fixture_groups()

    def test_refresh_updates_entry_after_new_commit(self, tmp_path, monkeypatch):
        src, sha1, sha2 = make_source_repo(tmp_path, extra_commit=True)
        assert sha2 and sha1 != sha2
        index_path = index_with_auto_entry(tmp_path, src, sha1)
        synced = install_env(monkeypatch, tmp_path, src)
        summary = ad.discover(index_path, dry_run=False)
        assert summary["added"] == []
        assert summary["refreshed"] == ["acme/plugin-alpha"]
        assert summary["refresh_failed"] == [] and summary["failures"] == []
        assert summary["index_updated"] is True and len(synced) == 1
        written = json.loads(index_path.read_text(encoding="utf-8"))
        entry = next(e for e in written["plugins"] if e["name"] == "acme/plugin-alpha")
        assert entry["status"] == "passed"
        assert entry["last_commit_sha"] == sha2

    def test_refresh_failure_marks_failed_keeps_sha_and_recovers(self, tmp_path, monkeypatch):
        src, sha1, sha2 = make_source_repo(tmp_path, extra_commit=True)
        index_path = index_with_auto_entry(tmp_path, src, sha1)
        install_env(monkeypatch, tmp_path, src, broken=["phase_barrier.languages:alpha"])
        summary = ad.discover(index_path, dry_run=False)
        assert summary["refreshed"] == []
        assert len(summary["refresh_failed"]) == 1
        assert "phase_barrier.languages:alpha" in summary["refresh_failed"][0]["reason"]
        written = json.loads(index_path.read_text(encoding="utf-8"))
        entry = next(e for e in written["plugins"] if e["name"] == "acme/plugin-alpha")
        assert entry["status"] == "failed"
        assert entry["last_commit_sha"] == sha1  # 保留最近一次通过验证的 SHA
        # 上游修复后下一轮重试成功
        install_env(monkeypatch, tmp_path, src, broken=None)
        summary = ad.discover(index_path, dry_run=False)
        assert summary["refreshed"] == ["acme/plugin-alpha"]
        written = json.loads(index_path.read_text(encoding="utf-8"))
        entry = next(e for e in written["plugins"] if e["name"] == "acme/plugin-alpha")
        assert entry["status"] == "passed"
        assert entry["last_commit_sha"] == sha2

    def test_discovery_failure_not_indexed(self, tmp_path, monkeypatch):
        src, _, _ = make_source_repo(tmp_path)
        index_path = empty_index(tmp_path)
        install_env(monkeypatch, tmp_path, src, broken=["phase_barrier.validators:require_alpha_spec"])
        summary = ad.discover(index_path, dry_run=False)
        assert summary["added"] == []
        assert len(summary["failures"]) == 1
        assert summary["index_updated"] is False
        written = json.loads(index_path.read_text(encoding="utf-8"))
        assert all(e["name"] != "acme/plugin-alpha" for e in written["plugins"])