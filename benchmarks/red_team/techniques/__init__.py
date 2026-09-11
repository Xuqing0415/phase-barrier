"""逃逸技术库（路径 1）：八类技术，对应五道防线的主要绕过面。

| 技术 | 目标防线 | 攻击面 |
|------|----------|--------|
| ``template_filler`` | 1 | 模板字段齐全但语义为空 |
| ``coverage_disguise`` | 2A | 需求条目「提及」而非「覆盖」 |
| ``constraint_weaken`` | 2B | 定量约束被改写成程度词 |
| ``constraint_replace`` | 2B | 约束对象被偷换成更弱的同类对象 |
| ``formal_evade`` | 3 | 变量别名 + 跨变量拆分的矛盾 |
| ``equivalent_op`` | 4 | 等价操作绕过关键字规则库 |
| ``timing_evade`` | 4 | 绕过工具包装器，危险操作不进 trace |
| ``risk_camouflage`` | 5 | 高风险任务拆成多个低风险子任务 |
| ``tool_chain_script`` | 4 | 写一个不被扫描的脚本（.tmp/.env/Makefile）再执行它 |
| ``deliverable_truncation`` | 4 | 危险操作藏在 trace 4000 字符截断点之后 |
| ``obfuscated_exec`` | 4 | 引号拼接 / 反斜杠转义 / base64 解码后管道给 sh |
| ``stage_skip_delivery`` | 4 | 跳过中间阶段直接交付 |
| ``chain_*``（3 条） | 4 | 多步攻击链：弱测试 / spec 漂移 / 打包外传 |
| ``session_family_*``（3 条） | 5 | 会话级攻击：同族低风险子任务累积 |
"""
from __future__ import annotations

from .base import (
    DEFENSE_LABELS,
    Defense,
    EscapeAttempt,
    EscapeResult,
    EscapeTechnique,
    Outcome,
    Requirement,
    ToolCall,
)
from .constraint_replace import ConstraintReplaceTechnique
from .constraint_weaken import ConstraintWeakenTechnique
from .coverage_disguise import CoverageDisguiseTechnique
from .equivalent_op import EquivalentOpTechnique
from .formal_evade import FormalEvadeTechnique
from .risk_camouflage import RiskCamouflageTechnique
from .template_filler import TemplateFillerTechnique
from .chains import CHAIN_TECHNIQUES, ExfilChain, SpecDriftChain, TestCoverageChain
from .misc_attacks import MISC_TECHNIQUES, DeliverableTruncationTechnique, StageSkipTechnique
from .obfuscated import ObfuscatedExecTechnique
from .session_attacks import (
    SESSION_TECHNIQUES,
    SessionFamilyBelowThresholdTechnique,
    SessionFamilySplitTechnique,
    SessionFamilyStaggerTechnique,
)
from .timing_evade import TimingEvadeTechnique
from .tool_chains import ToolChainTechnique

#: 全部内置技术（顺序 = 报告中的展示顺序）
ALL_TECHNIQUES = (
    TemplateFillerTechnique(),
    CoverageDisguiseTechnique(),
    ConstraintWeakenTechnique(),
    ConstraintReplaceTechnique(),
    FormalEvadeTechnique(),
    EquivalentOpTechnique(),
    TimingEvadeTechnique(),
    RiskCamouflageTechnique(),
    ToolChainTechnique(),
    DeliverableTruncationTechnique(),
    ObfuscatedExecTechnique(),
    StageSkipTechnique(),
) + CHAIN_TECHNIQUES + SESSION_TECHNIQUES

#: 按名字索引
TECHNIQUES_BY_NAME = {t.name: t for t in ALL_TECHNIQUES}

__all__ = [
    "ALL_TECHNIQUES",
    "TECHNIQUES_BY_NAME",
    "DEFENSE_LABELS",
    "Defense",
    "EscapeAttempt",
    "EscapeResult",
    "EscapeTechnique",
    "Outcome",
    "Requirement",
    "ToolCall",
    "ConstraintReplaceTechnique",
    "ConstraintWeakenTechnique",
    "CoverageDisguiseTechnique",
    "EquivalentOpTechnique",
    "FormalEvadeTechnique",
    "RiskCamouflageTechnique",
    "TemplateFillerTechnique",
    "TimingEvadeTechnique",
    "ToolChainTechnique",
    "DeliverableTruncationTechnique",
    "ObfuscatedExecTechnique",
    "StageSkipTechnique",
    "TestCoverageChain",
    "SpecDriftChain",
    "ExfilChain",
    "SessionFamilySplitTechnique",
    "SessionFamilyStaggerTechnique",
    "SessionFamilyBelowThresholdTechnique",
    "CHAIN_TECHNIQUES",
    "MISC_TECHNIQUES",
    "SESSION_TECHNIQUES",
]
