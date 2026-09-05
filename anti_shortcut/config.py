"""阶段门禁配置：Pydantic 模型 + YAML 加载。

配置允许项目自定义阶段划分、证据要求、文件模式、测试命令等，
从而在“严格门禁”与“灵活性”之间取得平衡（见方案第 5 章/第 10 章）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

# 阶段名称（与方案第 5 章保持一致）
STAGES: dict[int, str] = {
    0: "需求接收",
    1: "Spec 设计",
    2: "测试用例编写",
    3: "实现代码",
    4: "运行测试",
    5: "修复与回归",
    6: "交付",
}

# 阶段元数据：准入门槛与必需证据（与文档「阶段定义与证据要求」一致，v0.26.2）
# 供编排器 SDK ``PhaseBarrier.list_stages()`` 与 CLI 展示使用。
STAGE_META: dict[int, dict[str, str]] = {
    0: {"entry": "无", "evidence": "用户需求原文（由系统传入，自动记录）"},
    1: {"entry": "阶段 0 完成", "evidence": "spec.md 文件，包含必需章节"},
    2: {"entry": "阶段 1 完成", "evidence": "测试代码文件（如 test_*.py），至少 N 个测试函数且含断言"},
    3: {"entry": "阶段 2 完成", "evidence": "源代码文件（非测试文件），通过语法检查"},
    4: {"entry": "阶段 3 完成", "evidence": "测试执行命令成功（退出码 0）并输出测试结果"},
    5: {"entry": "阶段 4 测试未通过", "evidence": "修改后的源码 + 再次测试全部通过"},
    6: {"entry": "阶段 4/5 测试全部通过", "evidence": "交付总结（可选）或用户确认"},
}

DEFAULT_SPEC_SECTIONS = ["## 需求分析", "## 设计方案", "## 接口定义"]

# 默认测试命令正则（匹配命令前缀），可按语言扩展
DEFAULT_TEST_COMMANDS: list[str] = [
    r"^\s*python3?\s+(-m\s+)?pytest\b",
    r"^\s*pytest\b",
    r"^\s*python3?\s+-m\s+unittest\b",
    r"^\s*npm\s+test\b",
    r"^\s*npx\s+(jest|vitest|mocha|playwright)\b",
    r"^\s*(go|rust)\s+test\b",
    r"^\s*cargo\s+test\b",
    r"^\s*gradle\s+test\b",
    r"^\s*mvn\s+test\b",
    r"^\s*tox\b",
    r"^\s*unittest\b",
]

# 默认测试文件匹配模式（fnmatch / Path.match 语义）
DEFAULT_TEST_FILE_PATTERNS: list[str] = [
    "test_*.py",
    "*_test.py",
    "tests/**/test_*.py",
    "tests/**/*_test.py",
]

DEFAULT_SOURCE_FILE_PATTERNS: list[str] = ["*.py"]


class RequirementCoverageOptions(BaseModel):
    """需求追踪语义校验配置（v0.49.0）：spec REQ-xxx 须被测试引用。"""

    enabled: bool = False
    min_coverage: float = 100.0
    stages: list[int] = Field(default_factory=lambda: [2])

    @field_validator("min_coverage")
    @classmethod
    def _check_coverage_range(cls, value: float) -> float:
        if not (0 <= value <= 100):
            raise ValueError(f"semantic.requirement_coverage.min_coverage 必须是 0-100 的百分比，得到 {value}")
        return value

    @field_validator("stages")
    @classmethod
    def _check_stages(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("semantic 校验器 stages 不能为空")
        bad = [s for s in value if not isinstance(s, int) or isinstance(s, bool) or not (0 <= s <= 6)]
        if bad:
            raise ValueError(f"semantic 校验器 stages 必须是 0-6 的整数，得到 {bad}")
        return value


class MutationScoreOptions(BaseModel):
    """变异测试语义校验配置（v0.49.0，仅 Python）：存活变异体过多即测试质量不足。"""

    enabled: bool = False
    min_score: float = 80.0
    max_mutants: int = 20
    timeout_per_mutant: float = 60.0
    seed: int = 42
    python_bin: str | None = None
    command: list[str] | None = None
    stages: list[int] = Field(default_factory=lambda: [4])

    @field_validator("min_score")
    @classmethod
    def _check_score_range(cls, value: float) -> float:
        if not (0 <= value <= 100):
            raise ValueError(f"semantic.mutation_score.min_score 必须是 0-100 的百分比，得到 {value}")
        return value

    @field_validator("max_mutants")
    @classmethod
    def _check_max_mutants(cls, value: int) -> int:
        if value < 1:
            raise ValueError("semantic.mutation_score.max_mutants 必须 >= 1")
        return value

    @field_validator("timeout_per_mutant")
    @classmethod
    def _check_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("semantic.mutation_score.timeout_per_mutant 必须 > 0")
        return value

    @field_validator("stages")
    @classmethod
    def _check_stages(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("semantic 校验器 stages 不能为空")
        bad = [s for s in value if not isinstance(s, int) or isinstance(s, bool) or not (0 <= s <= 6)]
        if bad:
            raise ValueError(f"semantic 校验器 stages 必须是 0-6 的整数，得到 {bad}")
        return value


# v0.50.0：spec 套话 / 空话句式清单（命中数超过 max_filler_hits 即拒绝）。
# 语义层默认关闭；这些默认句式可被 config.semantic.spec_specificity.filler_patterns 覆盖。
DEFAULT_SPEC_FILLER_PATTERNS: list[str] = [
    r"采用(?:合适|合理|先进|优秀|业界领先|成熟)的(?:技术|方案|架构)",
    r"提供(?:完整|完善)的?(?:功能|接口|能力|解决方案)",
    r"实现一个(?:通用|完整|基础|功能)模块",
    r"满足(?:用户)?(?:全部|所有)?需求",
    r"确保(?:系统|模块|平台|产品)的(?:稳定|可靠|安全|高效|可扩展|高性能)性",
    r"(?:具体)?(?:实现|技术|设计)细节(?:将)?(?:在|留到)?(?:后续|稍后|实现阶段|开发中)(?:再)?(?:补充|细化|确定)",
    r"根据(?:用户)?需求(?:进行|做出)?(?:相应|合理|适当)的?(?:设计|调整|处理|规划)",
    r"综合考虑(?:各种)?因素",
]


class SpecSpecificityOptions(BaseModel):
    """spec 具体性语义校验配置（v0.50.0）：拒绝只有章节标题的“套话 spec”。"""

    enabled: bool = False
    # 代码相关具体实体（函数 / 类 / 变量赋值 / API 路径 / 反引号代码）最少数量
    min_entities: int = 5
    # 接口签名标记（def 行 / 函数·输入·输出·参数·返回·异常列表项 / API 端点）最少数量
    min_signatures: int = 2
    # “采用 X 避免 Y / 选择 X 而非 Y”式明确技术决策最少数量
    min_decision_phrases: int = 1
    # 用户原始需求锚点词（latin 标识符 + 中文双字领域词）至少命中数；0 关闭该子检查
    min_requirement_anchors: int = 2
    # 套话句式允许命中上限（0 = 命中任何默认句式即拒绝）
    max_filler_hits: int = 1
    filler_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_SPEC_FILLER_PATTERNS))
    stages: list[int] = Field(default_factory=lambda: [1])

    @field_validator("min_entities", "min_signatures", "min_decision_phrases",
                     "min_requirement_anchors", "max_filler_hits")
    @classmethod
    def _check_nonneg_int(cls, value: int, info) -> int:
        if isinstance(value, bool) or value < 0:
            raise ValueError(f"semantic.spec_specificity.{info.field_name} 必须 >= 0 的整数，得到 {value}")
        return value

    @field_validator("filler_patterns")
    @classmethod
    def _check_filler_patterns(cls, value: list[str]) -> list[str]:
        if not value or any(not isinstance(p, str) or not p.strip() for p in value):
            raise ValueError("semantic.spec_specificity.filler_patterns 必须是非空正则列表")
        return value

    @field_validator("stages")
    @classmethod
    def _check_stages(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("semantic 校验器 stages 不能为空")
        bad = [s for s in value if not isinstance(s, int) or isinstance(s, bool) or not (0 <= s <= 6)]
        if bad:
            raise ValueError(f"semantic 校验器 stages 必须是 0-6 的整数，得到 {bad}")
        return value


class TestAssertionQualityOptions(BaseModel):
    """测试断言质量语义校验配置（v0.50.0，仅 Python）：拒绝 `assert True` 等常数断言。"""

    enabled: bool = False
    # True：任何 test 函数只要含断言且全部为“纯常数断言”（不引用任何名称/调用）即拒绝
    strict: bool = True
    stages: list[int] = Field(default_factory=lambda: [2])

    @field_validator("stages")
    @classmethod
    def _check_stages(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("semantic 校验器 stages 不能为空")
        bad = [s for s in value if not isinstance(s, int) or isinstance(s, bool) or not (0 <= s <= 6)]
        if bad:
            raise ValueError(f"semantic 校验器 stages 必须是 0-6 的整数，得到 {bad}")
        return value


class SemanticOptions(BaseModel):
    """语义级校验总配置（v0.49.0 起，默认全部关闭，不影响既有门禁行为）。"""

    requirement_coverage: RequirementCoverageOptions = Field(default_factory=RequirementCoverageOptions)
    mutation_score: MutationScoreOptions = Field(default_factory=MutationScoreOptions)
    spec_specificity: SpecSpecificityOptions = Field(default_factory=SpecSpecificityOptions)
    test_assertion_quality: TestAssertionQualityOptions = Field(default_factory=TestAssertionQualityOptions)
    # 第三方语义校验器的自由配置（校验器按 config.semantic.<name>.enabled 开关）
    plugin_options: dict[str, Any] = Field(default_factory=dict)

class GateConfig(BaseModel):
    """反捷径校验 Skill 的可配置项。所有字段都有合理默认值，YAML 可部分覆盖。"""

    workspace: Path = Path(".")
    gate_dir_name: str = ".agent_gate"
    state_file_name: str = "state.json"
    audit_log_name: str = "audit.log"
    # ---- 阶段 1：Spec ----
    spec_file: str = "spec.md"
    spec_sections: list[str] = Field(default_factory=lambda: list(DEFAULT_SPEC_SECTIONS))
    spec_min_chars: int = 120
    # ---- 阶段 2：测试用例 ----
    test_file_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_FILE_PATTERNS))
    min_test_functions: int = 2
    require_assert_per_test: bool = True
    # ---- 阶段 3：实现 ----
    source_file_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_SOURCE_FILE_PATTERNS))
    require_implementation: bool = True
    # ---- 阶段 4/5：测试运行 ----
    test_commands: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_COMMANDS))
    max_test_output_tail: int = 4000
    # 覆盖率门禁（v0.7.0）：设为 0-100 的百分比后，阶段 4/5 要求测试输出包含
    # 覆盖率报告且不低于该阈值（pytest-cov / go test -cover / jest --coverage / vitest --coverage 等）
    coverage_threshold: float | None = None
    # ---- 语言适配（v0.3.0）----
    # 显式指定语言（如 "python" / "javascript"），优先级最高；None 时自动检测
    language: str | None = None
    # 自定义语言适配器导入路径（如 "my_package.module.MyAdapter"），或已注册的适配器名
    language_adapter: str | None = None
    # 传递给语言适配器的额外参数（如最少测试数），由适配器自行解释
    adapter_options: dict[str, Any] = Field(default_factory=dict)
    # ---- 状态签名（v0.8.0）----
    # HMAC-SHA256 密钥：设置后 state.json 写入 signature 并在加载时校验，
    # 防止不可信环境下 Agent 篡改状态；未设置时回退环境变量 PHASE_BARRIER_HMAC_KEY
    state_hmac_key: str | None = None
    # ---- 密钥轮换（v0.9.0）----
    # 轮换期仍接受的旧密钥列表（仅用于验证；签名始终使用 state_hmac_key），
    # 也可通过环境变量 PHASE_BARRIER_HMAC_KEYS（逗号 / 空白分隔）提供
    state_hmac_keys: list[str] = Field(default_factory=list)
    # ---- 证据签名（v0.9.0）----
    # 阶段推进时把证据文件哈希写入 .agent_gate/evidence_manifest.json（带 HMAC 签名），
    # 供交付 / CI 用 verify-evidence 对照工作区，检测事后篡改
    evidence_signing: bool = True
    # ---- 审计远程推送（v0.9.0）----
    # 设置后审计事件异步 POST 到该 HTTP 端点（SIEM / webhook），推送失败不影响门禁
    audit_remote_url: str | None = None
    audit_remote_token: str | None = None
    audit_remote_timeout: float = 5.0
    audit_remote_batch_size: int = 50
    audit_remote_max_queue: int = 1000
    audit_remote_flush_interval: float = 5.0
    # v0.10.0：自定义 CA 证书（PEM 文件路径），用于自建 SIEM 的 HTTPS 端点
    audit_remote_ca_bundle: str | None = None
    # v0.10.0：发送失败时的重试次数（指数退避 backoff * 2**attempt 秒）
    audit_remote_retries: int = 2
    audit_remote_backoff_factor: float = 0.5
    # v0.11.0：mTLS 客户端证书 / 私钥（PEM 文件路径），用于双向 TLS 的 SIEM 端点
    audit_remote_client_cert: str | None = None
    audit_remote_client_key: str | None = None
    # v0.11.0：自定义请求头（合并到每次 POST；token 仍以 Authorization 头优先）
    audit_remote_headers: dict[str, str] = Field(default_factory=dict)
    # v0.11.0：持久化重试队列目录：发送失败的事件落盘为 JSONL，
    # 进程重启后自动恢复重发（适合 K8s 滚动重启 / 进程崩溃场景）
    audit_remote_spool_dir: str | None = None
    # ---- 安全 ----
    protect_gate_dir: bool = True
    # 允许 Agent 直接写入“其他”类型文件（如 README.md、docs），默认不限
    allow_other_files_any_stage: bool = True
    # ---- 内置安全规则包（v0.26.0）----
    # 按名称启用内置拦截规则（如 no_path_traversal / no_shell_injection /
    # no_hardcoded_secrets / require_license_header），空列表时不启用
    rules: list[str] = Field(default_factory=list)
    # 传递给内置规则的额外选项（如 require_license_header 的 license_header 文本）
    rules_options: dict[str, Any] = Field(default_factory=dict)
    # ---- 语义级校验（v0.49.0，默认关闭）----
    # 结构校验之上的语义增强：需求追踪（REQ -> 测试引用）与 Python 变异测试；
    # 默认全部 disabled，启用后不满足即阻止阶段推进（详见 docs/semantic-validation.md）
    semantic: SemanticOptions = Field(default_factory=SemanticOptions)

    @model_validator(mode="after")
    def _expand_workspace(self) -> "GateConfig":
        self.workspace = Path(self.workspace).expanduser().resolve()
        return self

    @field_validator("coverage_threshold")
    @classmethod
    def _validate_coverage_threshold(cls, value: float | None) -> float | None:
        """v0.16.0：覆盖率阈值必须为 0-100 的百分比，拒绝越界值（如 150 / -10）。"""
        if value is not None and not (0 <= value <= 100):
            raise ValueError(f"coverage_threshold 必须是 0-100 的百分比，得到 {value}")
        return value


def load_config(path: str | Path | dict[str, Any] | None = None) -> GateConfig:
    """从 YAML 文件或字典加载配置，缺失字段使用默认值。

    - ``None``：纯默认配置
    - ``GateConfig``：原样返回（供 Skill 直接复用已加载的配置）
    - ``dict``：字段覆盖
    - ``Path``/``str``：读取 YAML 文件后覆盖
    """
    if path is None:
        data: dict[str, Any] = {}
    elif isinstance(path, GateConfig):
        return path
    elif isinstance(path, (str, Path)):
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"配置文件不存在: {p}")
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ValueError(f"配置文件 {p} 顶层必须是映射（dict）")
    elif isinstance(path, dict):
        data = path
    else:
        raise TypeError(f"不支持的配置类型: {type(path)!r}")
    return GateConfig(**data)
