"""plugins.json 索引自动验证脚本测试（v0.45.0）。

覆盖：索引 schema、入口点声明展开 / 对照检查、安装失败传播、
plugin-verify 子进程 JSON 解析、--update 写回、main 退出码。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_plugins.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_plugins_index", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vp = _load_module()


def _entry(name: str = "demo-plugin", **overrides) -> dict:
    base = {
        "name": name,
        "repo": "./examples/custom_adapter",
        "install": "./examples/custom_adapter",
        "entry_points": {"phase_barrier.languages": ["foo"]},
        "last_verified": "2026-01-01T00:00:00Z",
        "status": "passed",
    }
    base.update(overrides)
    return base


def _ok(group: str, name: str) -> dict:
    return {"ok": True, "plugins": {group: {name: {"ok": True, "errors": []}}}}


class TestIndexSchema:
    def test_root_plugins_json_exists_with_entries(self):
        index = vp.load_index(vp.DEFAULT_INDEX)
        assert isinstance(index, list) and len(index) >= 1
        for entry in index:
            assert entry["name"]
            assert entry["repo"]
            assert entry["entry_points"]
            assert entry["status"] in ("passed", "failed", "unverified")
            assert entry["last_verified"]

    def test_index_entry_local_repo_exists(self):
        index = vp.load_index(vp.DEFAULT_INDEX)
        for entry in index:
            raw = entry.get("install") or entry.get("repo") or ""
            if raw.startswith("./"):
                assert (vp.REPO_ROOT / raw).is_dir(), raw

    def test_index_entry_point_groups_are_known(self):
        index = vp.load_index(vp.DEFAULT_INDEX)
        known = set(vp.GROUP_ALIASES.values())
        for entry in index:
            for group, _names in vp._group_and_names(entry):
                assert group in known, group


class TestGroupAndNames:
    def test_dict_mapping_and_alias(self):
        entry = _entry(entry_points={"languages": ["foo"]})
        assert vp._group_and_names(entry) == [("phase_barrier.languages", ["foo"])]

    def test_list_shorthand(self):
        entry = _entry(entry_points=["phase_barrier.validators"])
        assert vp._group_and_names(entry) == [("phase_barrier.validators", [])]

    def test_empty_entry_points(self):
        assert vp._group_and_names(_entry(entry_points={})) == []


class TestCheckEntry:
    def test_all_declared_pass(self):
        entry = _entry(
            entry_points={
                "phase_barrier.validators": ["strict_design"],
                "phase_barrier.interceptors": ["deny_vendor"],
            }
        )
        results = {
            "phase_barrier.validators": {"strict_design": {"ok": True, "errors": []}},
            "phase_barrier.interceptors": {"deny_vendor": {"ok": True, "errors": []}},
        }
        assert vp.check_entry(entry, results) == []

    def test_missing_entry_reported(self):
        assert vp.check_entry(_entry(), {}) != []

    def test_broken_entry_reported(self):
        results = {"phase_barrier.languages": {"foo": {"ok": False, "errors": ["缺少方法"]}}}
        failures = vp.check_entry(_entry(), results)
        assert any("缺少方法" in f for f in failures)

    def test_group_shorthand_needs_ok_entry(self):
        entry = _entry(entry_points=["phase_barrier.languages"])
        assert vp.check_entry(entry, {"phase_barrier.languages": {"bar": {"ok": True, "errors": []}}}) == []
        assert vp.check_entry(entry, {"phase_barrier.languages": {"bar": {"ok": False, "errors": ["x"]}}}) != []


class TestInstallAndRun:
    def test_install_entry_ok(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""),
        )
        assert vp.install_entry(_entry(install="./examples/custom_adapter")) is None

    def test_install_entry_failure_message(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="boom line"),
        )
        err = vp.install_entry(_entry(install="./examples/custom_adapter"))
        assert err is not None and "pip install 失败" in err and "boom" in err

    def test_run_plugin_verify_parses_json(self, monkeypatch):
        payload = json.dumps({"ok": True, "plugins": {"phase_barrier.languages": {"foo": {"ok": True}}}}, ensure_ascii=False)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=payload, stderr=""),
        )
        report = vp.run_plugin_verify()
        assert report["ok"] is True
        assert "foo" in report["plugins"]["phase_barrier.languages"]

    def test_run_plugin_verify_nonzero_raises(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 3, stdout="", stderr="bad"),
        )
        with pytest.raises(RuntimeError, match="plugin-verify"):
            vp.run_plugin_verify()

    def test_run_plugin_verify_bad_json_raises(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="not json", stderr=""),
        )
        with pytest.raises(RuntimeError, match="JSON"):
            vp.run_plugin_verify()


class TestVerifyIndex:
    def test_all_passed(self, monkeypatch):
        monkeypatch.setattr(vp, "install_entry", lambda entry: None)
        monkeypatch.setattr(vp, "run_plugin_verify", lambda: _ok("phase_barrier.languages", "foo"))
        report = vp.verify_index([_entry()], install=True)
        assert report["ok"] is True
        assert report["plugins"]["demo-plugin"]["status"] == "passed"

    def test_missing_plugin_fails(self, monkeypatch):
        monkeypatch.setattr(vp, "install_entry", lambda entry: None)
        monkeypatch.setattr(vp, "run_plugin_verify", lambda: {})
        report = vp.verify_index([_entry()], install=True)
        assert report["ok"] is False
        assert report["plugins"]["demo-plugin"]["status"] == "failed"
        assert "未注册" in report["plugins"]["demo-plugin"]["detail"]

    def test_install_error_recorded(self, monkeypatch):
        monkeypatch.setattr(vp, "install_entry", lambda entry: "pip install 失败: boom")
        monkeypatch.setattr(vp, "run_plugin_verify", lambda: {})
        report = vp.verify_index([_entry()], install=True)
        assert report["plugins"]["demo-plugin"]["status"] == "failed"
        assert "boom" in report["plugins"]["demo-plugin"]["detail"]

    def test_unverified_without_entry_points(self, monkeypatch):
        entry = _entry(entry_points={})
        monkeypatch.setattr(vp, "run_plugin_verify", lambda: {})
        report = vp.verify_index([entry], install=False)
        assert report["plugins"]["demo-plugin"]["status"] == "unverified"
        assert report["ok"] is True


class TestUpdateIndex:
    def test_update_writes_status_and_timestamp(self, tmp_path):
        index_path = tmp_path / "plugins.json"
        index = [_entry(last_verified="2026-01-01T00:00:00Z", status="passed")]
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        report = {
            "verified_at": "2026-09-05T00:00:00Z",
            "ok": False,
            "plugins": {
                "demo-plugin": {"status": "failed", "entry_points": [], "detail": "boom"}
            },
        }
        vp.update_index(index_path, index, report)
        written = json.loads(index_path.read_text(encoding="utf-8"))
        assert written[0]["status"] == "failed"
        assert written[0]["last_verified"] == "2026-09-05T00:00:00Z"
        assert written[0]["name"] == "demo-plugin"  # 其余字段保留

    def test_update_skips_unverified(self, tmp_path):
        index_path = tmp_path / "plugins.json"
        index = [_entry(entry_points={}, status="unverified", last_verified=None)]
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        report = {
            "verified_at": "2026-09-05T00:00:00Z",
            "ok": True,
            "plugins": {"demo-plugin": {"status": "unverified", "entry_points": [], "detail": ""}},
        }
        vp.update_index(index_path, index, report)
        written = json.loads(index_path.read_text(encoding="utf-8"))
        assert written[0]["last_verified"] is None


class TestMainCli:
    def test_main_update_json_exit_codes(self, tmp_path, monkeypatch, capsys):
        index_path = tmp_path / "plugins.json"
        index = [_entry()]
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(
            vp,
            "verify_index",
            lambda index, *, install: {
                "verified_at": "2026-09-05T00:00:00Z",
                "ok": True,
                "plugins": {"demo-plugin": {"status": "passed", "entry_points": [], "detail": "ok"}},
            },
        )
        rc = vp.main(["--index", str(index_path), "--update", "--json", "--no-install"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["ok"] is True
        written = json.loads(index_path.read_text(encoding="utf-8"))
        assert written[0]["status"] == "passed"
        assert written[0]["last_verified"] == "2026-09-05T00:00:00Z"

    def test_main_failure_exit_code(self, tmp_path, monkeypatch):
        index_path = tmp_path / "plugins.json"
        index_path.write_text(json.dumps([_entry()], ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(
            vp,
            "verify_index",
            lambda index, *, install: {
                "verified_at": "2026-09-05T00:00:00Z",
                "ok": False,
                "plugins": {"demo-plugin": {"status": "failed", "entry_points": [], "detail": "boom"}},
            },
        )
        assert vp.main(["--index", str(index_path), "--no-install"]) == 1
