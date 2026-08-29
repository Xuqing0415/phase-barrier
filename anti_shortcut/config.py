"""阶段门禁配置：Pydantic 模型 + YAML 加载。

配置允许项目自定义阶段划分、证据要求、文件模式、测试命令等，
从而在“严格门禁”与“灵活性”之间取得平衡（见方案第 5 章/第 10 章）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

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
    # ---- 安全 ----
    protect_gate_dir: bool = True
    # 允许 Agent 直接写入“其他”类型文件（如 README.md、docs），默认不限
    allow_other_files_any_stage: bool = True

    @model_validator(mode="after")
    def _expand_workspace(self) -> "GateConfig":
        self.workspace = Path(self.workspace).expanduser().resolve()
        return self


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
