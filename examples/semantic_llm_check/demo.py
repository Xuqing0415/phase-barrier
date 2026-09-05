# -*- coding: utf-8 -*-
"""LLM 语义审查参考插件演示。

运行：
    python examples/semantic_llm_check/demo.py            # 进程内注册（默认无 api_key -> 离线降级跳过）
    pip install -e examples/semantic_llm_check            # 安装为入口点插件
    python examples/semantic_llm_check/demo.py --via-entry-point

配置（config.yaml，可选启用）：
    semantic:
      plugin_options:
        llm_semantic_check:
          enabled: true
          model: gpt-4o-mini
          # api_key: sk-...            # 或环境变量 LLM_SEMANTIC_CHECK_API_KEY / OPENAI_API_KEY
          # endpoint: https://api.openai.com/v1/chat/completions
          # network_required: true     # true 时模型判定不一致即阻断；默认 false 只提示不阻断
"""
import argparse
import json
import os
import sys
from pathlib import Path

from anti_shortcut import GateConfig, register_semantic_validator
from anti_shortcut.semantic import load_semantic_plugins, run_semantic_checks

sys.path.insert(0, str(Path(__file__).parent))
from llm_semantic_check import LLMSemanticCheck  # noqa: E402

SPEC = """# 示例 Spec

## 需求分析
- REQ-001: 加法函数 add(a, b) 返回两数之和

## 设计方案
直接返回 a + b，无副作用。

## 接口定义
def add(a: int, b: int) -> int
"""

TESTS = """from add import add


def test_add_positive():
    assert add(1, 2) == 3
"""

SOURCE = """def add(a, b):
    return a + b
"""




def _make_workdir() -> Path:
    """创建 0o755 临时工作区（兼容部分 Windows 沙箱对 0o700 目录加拒绝 ACL）。"""
    import tempfile

    base = Path(tempfile.gettempdir())
    for _ in range(20):
        candidate = base / ("pb-llm-demo-{0}-{1}".format(os.getpid(), os.urandom(4).hex()))
        try:
            os.makedirs(candidate, mode=0o755)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError("无法创建演示工作区")


def main() -> int:

    parser = argparse.ArgumentParser(description="LLM 语义审查参考插件演示")
    parser.add_argument("--via-entry-point", action="store_true", help="经入口点加载（需先 pip install -e）")
    parser.add_argument("--api-key", default=None, help="OpenAI 兼容 API Key（默认读环境变量）")
    parser.add_argument("--network-required", action="store_true", help="网络不可用/不一致时阻断（默认降级跳过）")
    args = parser.parse_args()

    if args.via_entry_point:
        loaded = load_semantic_plugins()
        assert any(v.name == "llm_semantic_check" for v in loaded), "入口点插件未加载：请先 pip install -e examples/semantic_llm_check"
        print("入口点已加载：llm_semantic_check")
    else:
        register_semantic_validator(LLMSemanticCheck())

    ws = _make_workdir()
    (ws / "spec.md").write_text(SPEC, encoding="utf-8")
    (ws / "test_add.py").write_text(TESTS, encoding="utf-8")
    (ws / "add.py").write_text(SOURCE, encoding="utf-8")

    cfg = GateConfig(spec_file="spec.md")
    opts = {
        "enabled": True,
        "network_required": bool(args.network_required),
    }
    if args.api_key:
        opts["api_key"] = args.api_key
    cfg.semantic.plugin_options["llm_semantic_check"] = opts

    ok, msg, evidence = run_semantic_checks(ws, cfg, object(), 2)
    print("result ok:", ok)
    print("message:", msg)
    print("evidence:", json.dumps(evidence, ensure_ascii=False, indent=2)[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())