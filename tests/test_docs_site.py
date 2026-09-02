"""v0.29.0 文档站冒烟测试：mkdocs 配置存在且可 strict 构建。"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _mkdocs_importable() -> bool:
    try:
        import mkdocs  # noqa: F401

        return True
    except ImportError:
        return False


mkdocs_missing = pytest.mark.skipif(
    shutil.which("mkdocs") is None and not _mkdocs_importable(),
    reason="mkdocs 未安装（pip install -e '.[dev]'）",
)


def test_mkdocs_config_exists():
    assert (REPO_ROOT / "mkdocs.yml").is_file()


def _iter_nav_pages(items):
    for entry in items:
        for _label, page in entry.items():
            if isinstance(page, list):
                yield from _iter_nav_pages(page)
            else:
                yield page


def test_mkdocs_nav_pages_exist():
    import yaml

    cfg = yaml.safe_load((REPO_ROOT / "mkdocs.yml").read_text(encoding="utf-8"))
    docs = REPO_ROOT / "docs"
    for page in _iter_nav_pages(cfg["nav"]):
        assert (docs / page).is_file(), f"nav 页面缺失: {page}"


@mkdocs_missing
def test_mkdocs_build_strict(tmp_path):
    proc = subprocess.run(
        [
            "python", "-m", "mkdocs", "build",
            "-f", str(REPO_ROOT / "mkdocs.yml"),
            "--strict",
            "--site-dir", str(tmp_path / "site"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, (
        f"mkdocs --strict 构建失败\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert (tmp_path / "site" / "index.html").is_file()
