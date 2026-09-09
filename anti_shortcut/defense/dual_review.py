"""防线 2：双向语义校验（双模型交叉复核，阶段 1 -> 2，v0.52.0）。

两个互相独立的校验实例分别从相反方向核对 spec：

- 实例 A（正向覆盖核查）：需求的每个目标 / 禁止行为 / 接口 / 验收条目是否在
  spec 中有对应的设计描述（covered / partially_covered / missing）；
- 实例 B（反向篡改核查）：spec 是否存在对需求约束的「新增 / 删减 / 替换」。

两侧同时 pass 才放行；任一实例调用失败默认拒绝（fail-closed），不允许静默放行。
两侧应配置不同的模型 / 服务商，提示词完全独立，避免同源幻觉互相印证。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from ..config import DualReviewModelOptions, DualReviewOptions, GateConfig
from ._base import DefenseCheckResult, DefenseLine
from ._common import now_iso, write_evidence

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


def _load_prompt(prompt_file: str, default: str) -> str:
    """读取提示词文件；未配置或文件缺失时回退到内置默认提示词。"""
    if prompt_file:
        p = Path(prompt_file)
        if p.is_file():
            return p.read_text(encoding="utf-8")
    builtin = Path(__file__).with_name("prompts")
    if prompt_file:
        cand = builtin / Path(prompt_file).name
        if cand.is_file():
            return cand.read_text(encoding="utf-8")
    return default


def _extract_json_object(text: str) -> dict[str, Any]:
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        raise ValueError("模型输出中未找到 JSON 对象")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("模型输出 JSON 不是对象")
    return data


def _call_model(
    cfg: DualReviewModelOptions, messages: list[dict[str, str]], timeout_seconds: float
) -> dict[str, Any]:
    """调用 OpenAI 兼容的 chat/completions 端点，返回解析后的 JSON 对象。

    默认端点 / 模型 / API key 均可在 ``defense.dual_review.reviewer_a/b`` 覆盖；
    只需把 ``endpoint`` 指向兼容 OpenAI 协议的网关（DeepSeek / OpenRouter / 自建等）。
    """
    key = os.environ.get(cfg.api_key_env, "")
    if not key:
        raise RuntimeError(f"未配置环境变量 {cfg.api_key_env}")
    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": 0.0,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        cfg.endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("模型响应缺少 choices")
    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("模型响应缺少 message.content")
    return _extract_json_object(content)


def _coerce_verdict(obj: dict[str, Any]) -> str:
    verdict = str(obj.get("verdict", "")).strip().lower()
    if verdict not in ("pass", "fail"):
        raise ValueError(f"verdict 必须是 pass / fail，得到 {verdict!r}")
    return verdict


def _requirement_text(requirement: Any, user_request: str) -> str:
    if isinstance(requirement, dict):
        return yaml.safe_dump(requirement, allow_unicode=True, sort_keys=False)
    return user_request


DEFAULT_PROMPT_A = """你是「需求覆盖核查员」。请把用户需求逐条与 spec 文档比对，
检查每个目标、禁止行为、输出接口、验收标准是否在 spec 中有对应的设计描述。
只输出一个 JSON 对象（不要任何其他文字），结构如下：
{"verdict": "pass" 或 "fail",
 "coverage": [{"item": "需求条目简述", "status": "covered|partially_covered|missing", "reason": "判断理由"}],
 "confidence": 0.0 到 1.0,
 "summary": "一句话总评"}
只要存在 missing 条目即 verdict=fail。不要因为 spec 措辞漂亮就放行，以事实为准。
"""

DEFAULT_PROMPT_B = """你是「约束篡改核查员」。请把 spec 与用户需求比对，逐条检查
spec 是否存在以下三类篡改：
1) added_constraint：需求未要求、但 spec 额外加入的限制（擅自扩大约束）；
2) removed_constraint：需求明确要求、但 spec 未提及或明显弱化（如 8 位写成 6 位）；
3) replaced_constraint：需求说 A，spec 写成语义不同的 B。
只输出一个 JSON 对象（不要任何其他文字），结构如下：
{"verdict": "pass" 或 "fail",
 "tamper": [{"type": "added_constraint|removed_constraint|replaced_constraint",
             "requirement": "被篡改的需求原文", "spec_text": "spec 中的对应表述",
             "reason": "为什么构成篡改"}],
 "confidence": 0.0 到 1.0,
 "summary": "一句话总评"}
只要存在任何 tamper 条目即 verdict=fail。没有把握时倾向严格（宁严勿纵）。
"""


class DualReviewLine(DefenseLine):
    """防线 2 实现：阶段 1 -> 2 双模型交叉复核。"""

    name = "dual_review"
    trigger = ((1, 2),)
    # 测试可注入假客户端：callable(cfg, messages, timeout) -> parsed dict
    client_a: Any = None
    client_b: Any = None

    def run(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        from_stage: int,
        to_stage: int,
    ) -> DefenseCheckResult:
        opts: DualReviewOptions = config.defense.dual_review
        spec_path = workspace / config.spec_file
        if not spec_path.is_file():
            return DefenseCheckResult(
                False,
                "防线 2（双模型复核）未通过：工作区缺少 spec 文件 "
                f"{config.spec_file}，无法交叉复核",
                {"error": "missing_spec"},
            )
        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
        requirement = state.get_evidence("requirement_template")
        user_request = str(state.get_evidence("user_request") or "")
        req_text = _requirement_text(requirement, user_request)

        prompt_a = _load_prompt(opts.reviewer_a.prompt_file, DEFAULT_PROMPT_A)
        prompt_b = _load_prompt(opts.reviewer_b.prompt_file, DEFAULT_PROMPT_B)

        def messages_for(role_prompt: str) -> list[dict[str, str]]:
            return [
                {"role": "system", "content": role_prompt},
                {
                    "role": "user",
                    "content": f"【用户需求】\n{req_text}\n\n【spec 文档】\n{spec_text}",
                },
            ]

        callers = {
            "reviewer_a": self.client_a or _call_model,
            "reviewer_b": self.client_b or _call_model,
        }
        review_opts = {
            "reviewer_a": opts.reviewer_a,
            "reviewer_b": opts.reviewer_b,
        }
        raw: dict[str, Any] = {}
        parsed: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for key in ("reviewer_a", "reviewer_b"):
            cfg = review_opts[key]
            for attempt in range(opts.max_retries + 1):
                try:
                    obj = callers[key](
                        cfg, messages_for(prompt_a if key == "reviewer_a" else prompt_b),
                        opts.timeout_seconds,
                    )
                    verdict = _coerce_verdict(obj)
                    confidence = float(obj.get("confidence", 0.0))
                    parsed[key] = {
                        "verdict": verdict,
                        "confidence": confidence,
                        "items": obj.get("coverage" if key == "reviewer_a" else "tamper", []),
                        "summary": str(obj.get("summary", "")),
                    }
                    raw[key] = obj
                    break
                except Exception as exc:
                    errors[key] = f"{exc.__class__.__name__}: {exc}"
                    if attempt < opts.max_retries:
                        time.sleep(0.5 * (attempt + 1))
            if key not in parsed:
                break

        evidence: dict[str, Any] = {
            "at": now_iso(),
            "spec_file": str(spec_path),
            "raw": raw,
            "parsed": parsed,
            "errors": errors,
        }
        evidence_path_ = write_evidence(workspace, config, "dual_review.json", evidence)
        evidence["evidence_file"] = str(evidence_path_)

        missing_keys = [k for k in ("reviewer_a", "reviewer_b") if k not in parsed]
        if missing_keys:
            detail = "；".join(f"{k}: {errors.get(k, '未知错误')}" for k in missing_keys)
            if opts.fail_closed:
                return DefenseCheckResult(
                    False,
                    f"防线 2（双模型复核）未通过：校验实例 {missing_keys} 调用失败"
                    f"（fail_closed=true 默认拒绝）——{detail}",
                    evidence,
                )
            return DefenseCheckResult(
                True,
                "防线 2（双模型复核）降级通过：调用失败但 fail_closed=false，"
                f"缺失实例 {missing_keys} 已记录到 evidence",
                evidence,
            )

        low_conf = []
        for key in ("reviewer_a", "reviewer_b"):
            conf = parsed[key]["confidence"]
            if conf < opts.min_confidence:
                low_conf.append(f"{key} 置信度 {conf} < {opts.min_confidence}")
        failures: list[str] = []
        for key in ("reviewer_a", "reviewer_b"):
            if parsed[key]["verdict"] != "pass":
                failures.append(f"[{key}] {parsed[key].get('summary', 'fail')}")
        failures.extend(low_conf)
        if failures:
            return DefenseCheckResult(
                False,
                "防线 2（双模型复核）未通过：" + "；".join(failures)
                + "。请按核查意见修订 spec 后重试",
                evidence,
            )
        return DefenseCheckResult(
            True,
            "防线 2（双模型复核）通过：覆盖核查（A）与篡改核查（B）同时 pass",
            evidence,
        )
