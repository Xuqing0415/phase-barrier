"""五道防线（v0.52.0）：在阶段门禁之上叠加多层语义 / 形式化 / 审计防线。

分层（按执行时序）：

1. ``requirement_template``：需求模板（阶段 0 -> 1）——自由文本需求规范化为
   「目标 / 禁止行为 / 接口定义 / 验收标准」四区块，封堵“隐含约束被自行解释”的源头；
2. ``dual_review``：双向语义校验（阶段 1 -> 2）——正向覆盖核查（A）+ 反向篡改
   核查（B）双模型交叉复核，识别 spec 对需求约束的新增 / 删减 / 替换；
3. ``formal_check``：形式化校验（阶段 1 -> 2）——约束 DSL（YAML）静态区间矛盾
   检测 + 可选 TLC 模型检查，识别自然语言看不出的逻辑矛盾；
4. ``behavior_audit``：事后链路审计（交付前）——trace.jsonl 工具调用轨迹与
   spec 声明的禁止操作比对，识别“文档合规但行为越界”；
5. ``human_review``：概率人工复核（交付前）——风险评分 + 确定性抽样，
   高价值 / 高风险任务强制人工复核，低风险自动放行。

所有防线默认关闭（``config.defense.*.enabled: false``），开启后叠加于既有门禁；
任一防线失败即阻止阶段推进。设计原则：单点可被绕过，但叠加后的逃逸成本最大化。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import GateConfig
from ._base import DefenseCheckResult, DefenseLine  # noqa: F401
from ._common import DEFENSE_DIR_NAME, defense_dir  # noqa: F401
from .behavior_audit import BehaviorAuditLine
from .dual_review import DualReviewLine
from .formal_check import FormalCheckLine
from .human_review import HumanReviewLine
from .requirement_template import RequirementTemplateLine

__all__ = [
    "DEFENSE_DIR_NAME",
    "defense_dir",
    "DefenseCheckResult",
    "DefenseLine",
    "BUILTIN_DEFENSE_LINES",
    "run_defense_checks",
]


# 防线注册顺序即执行顺序：前面防线失败即停，避免无意义的人工复核请求
BUILTIN_DEFENSE_LINES: list[DefenseLine] = [
    RequirementTemplateLine(),
    DualReviewLine(),
    FormalCheckLine(),
    BehaviorAuditLine(),
    HumanReviewLine(),
]


def run_defense_checks(
    workspace: Path,
    config: GateConfig,
    state: Any,
    from_stage: int,
    to_stage: int,
) -> tuple[bool, str, dict[str, Any]]:
    """在阶段推进时运行触发且启用的防线；任一失败即阻止推进。

    返回 ``(ok, message, evidence)``，与语义校验器签名一致，便于 Skill 统一处理。
    """
    results: list[dict[str, Any]] = []
    for line in BUILTIN_DEFENSE_LINES:
        if not line.in_trigger(from_stage, to_stage):
            continue
        if not line.enabled(config):
            continue
        try:
            res = line.run(workspace, config, state, from_stage, to_stage)
        except Exception as exc:  # 防线自身异常按失败处理（fail-closed）
            res = DefenseCheckResult(
                False,
                f"[{line.name}] 防线执行异常: {exc.__class__.__name__}: {exc}",
                {"error": f"{exc.__class__.__name__}: {exc}"},
            )
        results.append(
            {
                "line": line.name,
                "ok": res.ok,
                "message": res.message,
                "evidence": res.evidence,
                "request_id": res.request_id,
            }
        )
        if not res.ok:
            return False, res.message, {"defense_checks": results}
    if not results:
        return True, "", {}
    return (
        True,
        "五道防线通过（" + ", ".join(r["line"] for r in results) + "）",
        {"defense_checks": results},
    )
