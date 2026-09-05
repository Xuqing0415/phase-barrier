"""scripts/check_custom_domain.py 自定义域名检查测试（v0.48.0）。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_custom_domain.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_custom_domain", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ccd = _load_module()


class TestCheckCname:
    def test_missing_cname_warns_but_rc0(self, tmp_path, capsys):
        rc = ccd.main(["--cname", str(tmp_path / "CNAME")])
        out = capsys.readouterr().out
        assert rc == 0
        assert "WARN" in out and "未启用" in out

    def test_missing_cname_strict_rc1(self, tmp_path, capsys):
        rc = ccd.main(["--cname", str(tmp_path / "CNAME"), "--strict"])
        assert rc == 1
        assert "FAIL" in capsys.readouterr().out

    def test_matching_cname_ok(self, tmp_path, capsys):
        cname = tmp_path / "CNAME"
        cname.write_text("docs.phase-barrier.dev\n", encoding="utf-8")
        rc = ccd.main(["--cname", str(cname)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "OK" in out and "docs.phase-barrier.dev" in out

    def test_mismatch_warns_default_and_fails_strict(self, tmp_path, capsys):
        cname = tmp_path / "CNAME"
        cname.write_text("other.example.com", encoding="utf-8")
        rc = ccd.main(["--cname", str(cname)])
        assert rc == 0 and "WARN" in capsys.readouterr().out
        rc = ccd.main(["--cname", str(cname), "--strict"])
        assert rc == 1 and "FAIL" in capsys.readouterr().out

    def test_custom_domain_argument(self, tmp_path, capsys):
        cname = tmp_path / "CNAME"
        cname.write_text("docs.example.dev", encoding="utf-8")
        rc = ccd.main(["--cname", str(cname), "--domain", "docs.example.dev"])
        assert rc == 0 and "docs.example.dev" in capsys.readouterr().out