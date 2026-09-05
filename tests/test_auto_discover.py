"""scripts/auto_discover_plugins.py 自动发现脚本测试（v0.46.0）。

覆盖：repo 归一化 / 去重、GitHub Search（含 403 限流）、clone->install->
plugin-verify 验证链路、dry-run 不写盘、--update 收录 + docs 同步、
失败记录、容器结构迁移与 auto_discovery 配置。
"""
from __future__ import annotations

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "auto_discover_plugins.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("auto_discover_plugins", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ad = _load_module()


def _repo(
    full: str = "owner/plugin-demo",
    html: str | None = None,
    clone: str | None = None,
    fork: bool = False,
    archived: bool = False,
) -> dict:
    html = html or f"https://github.com/{full}"
    return {
        "full_name": full,
        "html_url": html,
        "clone_url": clone or f"{html}.git",
        "fork": fork,
        "archived": archived,
    }


def _entry(name: str = "owner/plugin-demo", repo: str = "https://github.com/owner/plugin-demo", **overrides) -> dict:
    base = {
        "name": name,
        "repo": repo,
        "install": f"git+{repo}.git#egg=plugin-demo",
        "entry_points": {"phase_barrier.languages": ["demo_lang"]},
        "last_verified": "2026-09-05T00:00:00Z",
        "status": "passed",
        "auto_discovered": True,
        "last_commit_sha": "a" * 40,
    }
    base.update(overrides)
    return base


def _index_path(tmp_path: Path, container: dict | None = None) -> Path:
    path = tmp_path / "plugins.json"
    if container is None:
        container = {
            "plugins": [
                _entry(
                    name="phase-barrier-foo-adapter",
                    repo="./examples/custom_adapter",
                    install="./examples/custom_adapter",
                    entry_points={"phase_barrier.languages": ["foo"]},
                    auto_discovered=False,
                    last_commit_sha=None,
                ),
                _entry(),
            ],
            "auto_discovery": {"github_topic": "phase-barrier-plugin", "enabled": True},
        }
    path.write_text(json.dumps(container, ensure_ascii=False), encoding="utf-8")
    return path



class _FakeTempDir:
    """用工作区下 tmp_path 替代系统 %TEMP%，避免 Windows 沙箱下清理权限问题。"""

    def __init__(self, base: Path):
        self.name = str(base)

    def __enter__(self):
        return self.name

    def __exit__(self, *exc):
        return False


def _fake_tmp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        ad.tempfile,
        "TemporaryDirectory",
        lambda *a, **k: _FakeTempDir(tmp_path / "pbwork"),
    )


class TestNormalize:
    def test_https_and_git_suffix(self):
        assert ad._normalize_repo("https://github.com/Owner/Repo") == "owner/repo"
        assert ad._normalize_repo("https://github.com/owner/repo.git") == "owner/repo"
        assert ad._normalize_repo("git+https://github.com/owner/repo.git") == "owner/repo"
        assert ad._normalize_repo("git@github.com:owner/repo.git") == "owner/repo"

    def test_local_and_invalid(self):
        assert ad._normalize_repo("./examples/custom_adapter") is None
        assert ad._normalize_repo("") is None
        assert ad._normalize_repo("not-a-url") is None

    def test_existing_repo_keys(self):
        index = [
            _entry(repo="./examples/custom_adapter"),
            _entry(name="other", repo="https://github.com/a/b", install="git+https://github.com/a/b.git"),
        ]
        keys = ad.existing_repo_keys(index)
        assert "a/b" in keys
        assert all(not k.startswith(".") for k in keys)


class TestGithubSearch:
    def test_search_returns_items(self, monkeypatch):
        captured = {}

        class FakeResp:
            def __init__(self, payload):
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(self._payload).encode("utf-8")

        def fake_urlopen(req, timeout=30):
            captured["url"] = req.full_url
            captured["auth"] = req.get_header("Authorization")
            return FakeResp({"items": [{"full_name": "a/b"}, {"full_name": "c/d"}]})

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        items = ad.github_search("phase-barrier-plugin", token="tok123")
        assert len(items) == 2
        assert "q=topic%3Aphase-barrier-plugin" in captured["url"]
        assert captured["auth"] == "Bearer tok123"

    def test_search_http_error_raises(self, monkeypatch):
        def fake_urlopen(req, timeout=30):
            raise urllib.error.HTTPError(req.full_url, 403, "rate limit", None, None)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(ad.GithubApiError) as exc:
            ad.github_search("phase-barrier-plugin", token="tok")
        assert exc.value.status == 403


class TestCloneVerify:
    def _plugin_payload(self, groups: dict, broken: list[str] | None = None) -> str:
        plugins = {}
        discovered = {}
        broken = broken or []
        for group, names in groups.items():
            discovered[group] = [{"name": n, "value": "x"} for n in names]
            plugins[group] = {}
            for n in names:
                plugins[group][n] = {"ok": n not in broken, "errors": [] if n not in broken else ["boom"]}
        return json.dumps({"ok": not broken, "plugins": plugins, "discovered": discovered}, ensure_ascii=False)

    def _fake_run(self, monkeypatch, payload: str):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            joined = " ".join(cmd)
            if "rev-parse" in joined:
                return __import__("subprocess").CompletedProcess(cmd, 0, stdout="b" * 40, stderr="")
            if "plugin-verify" in joined:
                return __import__("subprocess").CompletedProcess(cmd, 0, stdout=payload, stderr="")
            if "-m" in cmd and "pip" in cmd:
                return __import__("subprocess").CompletedProcess(cmd, 0, stdout="", stderr="")
            return __import__("subprocess").CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ad, "_run", fake_run)
        return calls

    def test_ok_entry(self, tmp_path, monkeypatch):
        payload = self._plugin_payload({"phase_barrier.languages": ["demo_lang"]})
        self._fake_run(monkeypatch, payload)
        _fake_tmp(monkeypatch, tmp_path)
        ok, entry, reason = ad.clone_verify(_repo())
        assert ok and not reason
        assert entry["name"] == "owner/plugin-demo"
        assert entry["status"] == "passed"
        assert entry["auto_discovered"] is True
        assert entry["last_commit_sha"] == "b" * 40
        assert entry["install"].startswith("git+https://github.com/owner/plugin-demo.git")
        assert entry["entry_points"] == {"phase_barrier.languages": ["demo_lang"]}

    def test_no_entry_points(self, tmp_path, monkeypatch):
        payload = json.dumps({"ok": True, "plugins": {}, "discovered": {}}, ensure_ascii=False)
        self._fake_run(monkeypatch, payload)
        _fake_tmp(monkeypatch, tmp_path)
        ok, entry, reason = ad.clone_verify(_repo())
        assert not ok and not entry
        assert "未发现任何" in reason

    def test_broken_entry_rejected(self, tmp_path, monkeypatch):
        payload = self._plugin_payload({"phase_barrier.validators": ["strict_design"]}, broken=["strict_design"])
        self._fake_run(monkeypatch, payload)
        _fake_tmp(monkeypatch, tmp_path)
        ok, _entry, reason = ad.clone_verify(_repo())
        assert not ok
        assert "strict_design" in reason

    def test_pip_install_failure(self, tmp_path, monkeypatch):
        import subprocess
        _fake_tmp(monkeypatch, tmp_path)

        def fake_run(cmd, **kwargs):
            if "-m" in cmd and "pip" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="resolve failed")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(ad, "_run", fake_run)
        ok, _entry, reason = ad.clone_verify(_repo())
        assert not ok and "pip install 失败" in reason


class TestDiscover:
    def test_dry_run_no_write(self, tmp_path, monkeypatch):
        path = _index_path(tmp_path)
        monkeypatch.setattr(ad, "github_search", lambda *a, **k: [_repo("a/new-one"), _repo(fork=True), _repo("owner/plugin-demo")])

        def boom(*a, **k):
            raise AssertionError("dry-run 不应验证候选")

        monkeypatch.setattr(ad, "clone_verify", boom)
        summary = ad.discover(path, dry_run=True)
        assert summary["dry_run"] is True
        assert summary["repos_seen"] == 2  # fork 被过滤
        assert summary["already_indexed"] == 1
        assert summary["candidates"] == 1
        assert summary["candidate_repos"] == ["https://github.com/a/new-one"]
        assert summary["index_updated"] is False
        assert json.loads(path.read_text(encoding="utf-8"))["plugins"]  # 文件未被改写结构
        assert len(json.loads(path.read_text(encoding="utf-8"))["plugins"]) == 2

    def test_update_adds_and_syncs(self, tmp_path, monkeypatch):
        path = _index_path(tmp_path)
        monkeypatch.setattr(ad, "github_search", lambda *a, **k: [_repo("owner/plugin-demo"), _repo("owner/brand-new")])
        monkeypatch.setattr(ad, "clone_verify", lambda repo, **k: (True, _entry(name=repo["full_name"], repo=repo["html_url"]), ""))
        synced = []
        monkeypatch.setattr(ad.verify_plugins, "sync_index_docs", lambda index: synced.append(index))
        summary = ad.discover(path, dry_run=False)
        assert summary["added"] == ["owner/brand-new"]
        assert summary["index_updated"] is True and summary["docs_synced"] is True
        assert len(synced) == 1
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["auto_discovery"]["github_topic"] == "phase-barrier-plugin"  # 容器配置保留
        names = [e["name"] for e in written["plugins"]]
        assert "owner/brand-new" in names
        new_entry = next(e for e in written["plugins"] if e["name"] == "owner/brand-new")
        assert new_entry["auto_discovered"] is True
        assert new_entry["status"] == "passed"

    def test_update_failure_recorded_no_change(self, tmp_path, monkeypatch):
        path = _index_path(tmp_path)
        monkeypatch.setattr(ad, "github_search", lambda *a, **k: [_repo("owner/bad-one")])
        monkeypatch.setattr(ad, "clone_verify", lambda repo, **k: (False, {}, "pip install 失败: boom"))
        summary = ad.discover(path, dry_run=False)
        assert summary["added"] == []
        assert len(summary["failures"]) == 1
        assert "boom" in summary["failures"][0]["reason"]
        assert summary["index_updated"] is False
        written = json.loads(path.read_text(encoding="utf-8"))
        assert len(written["plugins"]) == 2

    def test_disabled_skips_search(self, tmp_path, monkeypatch):
        container = {
            "plugins": [],
            "auto_discovery": {"github_topic": "phase-barrier-plugin", "enabled": False},
        }
        path = _index_path(tmp_path, container)

        def boom(*a, **k):
            raise AssertionError("disabled 不应调用 GitHub")

        monkeypatch.setattr(ad, "github_search", boom)
        summary = ad.discover(path, dry_run=False)
        assert summary["enabled"] is False
        assert summary["index_updated"] is False

    def test_legacy_list_container_compat(self, tmp_path):
        path = tmp_path / "plugins.json"
        path.write_text(json.dumps([_entry(name="legacy")], ensure_ascii=False), encoding="utf-8")
        container = ad.verify_plugins.load_index_file(path)
        assert container["plugins"][0]["name"] == "legacy"
        assert container["auto_discovery"]["enabled"] is True
        assert len(ad.verify_plugins.load_index(path)) == 1


class TestMainCli:
    def test_main_dry_run_prints_candidates(self, tmp_path, monkeypatch, capsys):
        path = _index_path(tmp_path)
        monkeypatch.setattr(ad, "discover", lambda *a, **k: {
            "enabled": True, "topic": "phase-barrier-plugin", "dry_run": True,
            "repos_seen": 3, "already_indexed": 1, "candidates": 1,
            "candidate_repos": ["https://github.com/a/b"], "added": [],
            "failures": [], "index_updated": False, "docs_synced": False,
        })
        rc = ad.main(["--index", str(path), "--dry-run"])
        assert rc == 0
        assert "would-add" in capsys.readouterr().out

    def test_main_update_json_summary(self, tmp_path, monkeypatch, capsys):
        path = _index_path(tmp_path)
        monkeypatch.setattr(ad, "discover", lambda *a, **k: {
            "enabled": True, "topic": "phase-barrier-plugin", "dry_run": False,
            "repos_seen": 1, "already_indexed": 0, "candidates": 1,
            "candidate_repos": [], "added": ["owner/x"], "failures": [],
            "index_updated": True, "docs_synced": True,
        })
        rc = ad.main(["--index", str(path), "--update", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["added"] == ["owner/x"]

    def test_main_github_error_rc1(self, tmp_path, monkeypatch):
        path = _index_path(tmp_path)

        def boom(*a, **k):
            raise ad.GithubApiError(403, "rate limited")

        monkeypatch.setattr(ad, "discover", boom)
        assert ad.main(["--index", str(path), "--dry-run"]) == 1