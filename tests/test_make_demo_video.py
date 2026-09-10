"""scripts/make_demo_video.py 的纯函数测试（B2 演示视频生成器）。

不调用 ffmpeg：只校验时间轴一致性、字体选择与 drawtext 转义，这些正是踩过的坑
（场景窗重叠导致叠字、`%` 被当作表达式展开）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_demo_video.py"


def _load():
    spec = importlib.util.spec_from_file_location("make_demo_video", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


def test_timeline_is_valid():
    mod.validate_timeline()


def test_timeline_rejects_overlap():
    with pytest.raises(ValueError):
        mod.validate_timeline((("a", 0.0, 10.0), ("b", 9.0, 20.0)))


def test_timeline_rejects_empty_window():
    with pytest.raises(ValueError):
        mod.validate_timeline((("a", 5.0, 5.0),))


def test_scene_lookup_and_duration():
    start, end = mod.scene("terminal")
    assert start < end
    assert mod.DURATION == mod.SCENES[-1][2]
    with pytest.raises(KeyError):
        mod.scene("nope")


def test_has_cjk():
    assert mod.has_cjk("五道防线")
    assert mod.has_cjk("全角，逗号")
    assert not mod.has_cjk("plain ascii 123")


def test_drawtext_disables_percent_expansion():
    """`25%` 这类文本必须配 expansion=none，否则 ffmpeg 会报 Stray % 并吞内容。"""
    f = mod.drawtext_filter("line_000.txt", "mono.ttf", 20, "0xFFFFFF", 10, 20,
                            "between(t,0,3)")
    assert "expansion=none" in f
    assert "textfile=line_000.txt" in f
    assert "enable='between(t,0,3)'" in f


def test_drawtext_center_uses_expression():
    f = mod.drawtext_filter("l.txt", "mono.ttf", 20, "0xFFFFFF", 0, 20,
                            "between(t,0,3)", center=True)
    assert "x=(w-text_w)/2" in f


def test_timeline_entries_use_real_demo_lines_and_are_time_ordered():
    demo = ["[需求] 已记录用户需求，当前阶段：Spec 设计（stage 1）",
            "[尝试跳步] Agent 试图跳过 spec / 测试，直接写实现 fib.py ...",
            "  [拦截] 当前阶段不允许编写实现代码：请先完成测试用例编写（阶段 2）"]
    entries = mod.timeline_entries(demo)
    texts = [e["text"] for e in entries]
    # 真实输出必须原样出现在视频里（不伪造）
    assert any("Spec 设计（stage 1）" in t for t in texts)
    assert any("[拦截]" in t for t in texts)
    # 每段文字都有可判定为合法的时间窗
    for e in entries:
        assert e["enable"].startswith("between(t,")
        assert e["size"] > 0


def test_build_filtergraph_writes_textfiles_and_uses_cjk_font(tmp_path):
    entries = [
        {"text": "五道防线", "x": 10, "y": 20, "size": 27, "color": "0xFFFFFF",
         "enable": "between(t,1,2)", "center": False},
        {"text": "ascii only", "x": 10, "y": 40, "size": 19, "color": "0xFFFFFF",
         "enable": "between(t,1,2)", "center": False},
    ]
    graph = mod.build_filtergraph(entries, {"mono": "mono.ttf", "cjk": "cjk.ttc"}, tmp_path)
    assert (tmp_path / "line_000.txt").read_text(encoding="utf-8") == "五道防线"
    assert "fontfile=cjk.ttc" in graph
    assert "fontfile=mono.ttf" in graph
    # 终端面板底与边框必须早于文字绘制
    assert graph.index("drawbox") < graph.index("drawtext")
