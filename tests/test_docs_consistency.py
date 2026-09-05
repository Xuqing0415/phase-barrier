"""文档站 / 插件状态页一致性测试（v0.48.0）。

保证：
- ``docs/plugins.md``（指南）不再内嵌被自动同步覆盖的状态表；
- ``docs/plugin-status.md``（状态页）是唯一承载 ``plugins.json`` 渲染表格的页面；
- 状态页表格行与 ``plugins.json`` 条目一一对应。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCRIPT = REPO_ROOT / "scripts" / "verify_plugins.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_plugins_consistency", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vp = _load_module()


class TestDocsConsistency:
    def test_plugins_md_is_guide_without_synced_table(self):
        text = (REPO_ROOT / "docs" / "plugins.md").read_text(encoding="utf-8")
        assert "<!-- plugins-index:start -->" not in text
        assert "<!-- plugins-index:end -->" not in text
        # 指南应链接状态页而非自行维护表格
        assert "plugin-status.md" in text

    def test_plugin_status_md_has_single_marker_pair(self):
        path = REPO_ROOT / "docs" / "plugin-status.md"
        text = path.read_text(encoding="utf-8")
        assert text.count(vp.MARK_START) == 1
        assert text.count(vp.MARK_END) == 1
        start = text.index(vp.MARK_START) + len(vp.MARK_START)
        end = text.index(vp.MARK_END)
        assert "| 插件 | 来源 | 收录 | 入口点 | 状态 | 最近验证 | 提交 |" in text

    def test_status_rows_match_plugins_json(self):
        index = vp.load_index(vp.DEFAULT_INDEX)
        text = (REPO_ROOT / "docs" / "plugin-status.md").read_text(encoding="utf-8")
        start = text.index(vp.MARK_START) + len(vp.MARK_START)
        end = text.index(vp.MARK_END)
        block = text[start:end]
        # 判定：状态页表格应覆盖 plugins.json 全部条目
        for entry in index:
            name = entry.get("name") or "<unnamed>"
            assert name in block, f"状态页缺少插件条目 {name}"

    def test_default_sync_target_is_plugin_status(self):
        # v0.47.0 起默认同步目标是 docs/plugin-status.md
        assert vp.DOCS_PLUGIN_STATUS == REPO_ROOT / "docs" / "plugin-status.md"
        assert vp.DOCS_PLUGIN_STATUS.is_file()