# -*- coding: utf-8 -*-
"""LLM 语义审查参考插件（v0.49.0 示例，不参与默认安装）。

在结构校验之上用大语言模型做一次“需求 ↔ spec ↔ 测试 ↔ 实现”的语义一致性
审查，捕获可被模板化产物绕过的内容空泛问题。设计要点：

- 仅用标准库 urllib 调用 OpenAI 兼容的 ``/chat/completions`` 接口；
- **离线降级**：未配置 api_key / endpoint 或网络失败时返回“跳过”而非阻断
  （语义校验默认全部关闭，本插件更不应在误配置时拖垮门禁）；
- 启用后（``semantic.plugin_options.llm_semantic_check.enabled = true``），
  模型给出结构化结论不一致时才阻止阶段推进；
- 所有配置经 ``config.semantic.plugin_options["llm_semantic_check"]`` 传入，
  校验器只读不写，保证幂等。

运行示例见 ``demo.py``。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from anti_shortcut.semantic import SemanticCheckResult, SemanticValidator

DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """你是软件工程流程审查员。用户会给出一份 spec（含 REQ-xxx 需求条目）、
对应测试代码与实现代码。请判断：测试是否真正覆盖了 spec 声明的每个 REQ；实现是否与
spec 的接口定义一致；是否存在“形式完整但内容空泛”（如无断言测试、实现与 spec 脱节）。
只输出 JSON，不要其他文字，格式：
{"consistent": true 或 false, "reason": "一句话结论", "gaps": ["发现的语义鸿沟，可为空数组"]}
若无法判断（材料不足），请输出 {"consistent": true, "reason": "材料不足，不阻断", "gaps": []}。"""


def _section(content: str, limit: int = 3000) -> str:
    return content[:limit]


def build_user_prompt(spec_text: str, test_files: list[tuple[str, str]], source_files: list[tuple[str, str]]) -> str:
    """把工作区材料组装为审查提示。"""
    parts = [f"## Spec\n{_section(spec_text)}", "## 测试文件"]
    for name, text in test_files:
        parts.append(f"### {name}\n{_section(text)}")
    if not test_files:
        parts.append("（无）")
    parts.append("## 实现文件")
    for name, text in source_files:
        parts.append(f"### {name}\n{_section(text)}")
    if not source_files:
        parts.append("（无）")
    return "\n\n".join(parts)


def parse_model_output(content: str) -> dict[str, Any]:
    """解析模型返回，剥离 ```json 围栏与前后杂讯；解析失败返回保守不阻断。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        # 模型输出非法时降级为“不阻断”，避免误杀；原因写入 evidence
        return {"consistent": True, "reason": "模型输出无法解析，降级不阻断", "gaps": [], "parse_error": True}
    if not isinstance(data, dict):
        return {"consistent": True, "reason": "模型输出非对象，降级不阻断", "gaps": [], "parse_error": True}
    return {
        "consistent": bool(data.get("consistent", True)),
        "reason": str(data.get("reason", "")),
        "gaps": list(data.get("gaps", []) or []),
        "parse_error": False,
    }


class LLMSemanticCheck(SemanticValidator):
    """把 LLM 审查接入阶段推进门禁的可选参考实现。"""

    name = "llm_semantic_check"
    description = "LLM 语义一致性审查（需求 ↔ spec ↔ 测试 ↔ 实现），默认离线降级跳过"
    stages = (2, 4)

    def check(
        self,
        workspace: Path,
        config,
        state,
        adapter=None,
    ) -> SemanticCheckResult:
        opts = dict((config.semantic.plugin_options or {}).get(self.name, {}))
        api_key = opts.get("api_key") or os.environ.get("LLM_SEMANTIC_CHECK_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return SemanticCheckResult(
                True, "LLM 语义审查未配置 api_key，跳过（离线降级）", {"skipped": "no_api_key"}
            )
        spec_path = workspace / getattr(config, "spec_file", "spec.md")
        spec_text = spec_path.read_text(encoding="utf-8", errors="replace") if spec_path.exists() else ""
        if not spec_text.strip():
            return SemanticCheckResult(True, "无 spec 内容，LLM 语义审查跳过", {"skipped": "no_spec"})
        adapter = adapter or (None)
        test_files: list[tuple[str, str]] = []
        source_files: list[tuple[str, str]] = []
        if adapter is not None and hasattr(config, "spec_file"):
            from anti_shortcut.paths import iter_workspace_files

            for p in iter_workspace_files(workspace, config):
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                rel = str(p.relative_to(workspace))
                if adapter.is_test_file(p, config):
                    test_files.append((rel, text))
                elif adapter.is_source_file(p, config):
                    source_files.append((rel, text))
        payload = {
            "model": opts.get("model", DEFAULT_MODEL),
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(spec_text, test_files, source_files)},
            ],
        }
        endpoint = opts.get("endpoint", DEFAULT_ENDPOINT)
        timeout = float(opts.get("timeout", 30))
        try:
            data = self._post(endpoint, payload, api_key, timeout)
            verdict = parse_model_output(data)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return SemanticCheckResult(
                True,
                f"LLM 语义审查不可用（{exc.__class__.__name__}），降级跳过；"
                "如需强制阻断请配置 network_required: true",
                {"skipped": "network_error", "error": str(exc)},
            )
        evidence = {
            "endpoint": endpoint,
            "model": opts.get("model", DEFAULT_MODEL),
            "verdict": verdict,
        }
        consistent = bool(verdict.get("consistent", True))
        if opts.get("network_required") and not consistent:
            gaps = "；".join(verdict.get("gaps", []) or [])
            return SemanticCheckResult(
                False,
                f"LLM 语义审查未通过：{verdict.get('reason', '')}" + (f"（{gaps}）" if gaps else ""),
                evidence,
            )
        return SemanticCheckResult(
            True,
            "LLM 语义审查通过" if consistent else f"LLM 语义审查发现潜在问题（不阻断）：{verdict.get('reason', '')}",
            evidence,
        )

    @staticmethod
    def _post(endpoint: str, payload: dict, api_key: str, timeout: float) -> str:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (示例插件，端点由用户配置)
            body = resp.read().decode("utf-8")
        envelope = json.loads(body)
        return envelope["choices"][0]["message"]["content"]