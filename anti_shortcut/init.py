"""配置脚手架：``anti_shortcut init`` —— 为当前工作区生成 phase-barrier YAML 配置（v0.26.0）。

用法::

    python -m anti_shortcut init [--workspace .] [--language auto] [--output config.yaml] [--force]
                                 [--with-coverage] [--coverage-threshold 80]
                                 [--hmac-key KEY] [--audit-url URL] [--rules no_path_traversal,...]

生成的配置带注释，且保证能被 ``GateConfig`` 直接加载。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import DEFAULT_SPEC_SECTIONS, GateConfig
from .languages import detect_language

__all__ = ["LANGUAGE_PROFILES", "render_config", "init_config"]

# 各语言推荐配置模板（init 生成用）
LANGUAGE_PROFILES: dict[str, dict[str, Any]] = {
    "python": {
        "test_file_patterns": ["test_*.py", "*_test.py", "tests/**/test_*.py", "tests/**/*_test.py"],
        "source_file_patterns": ["*.py"],
        "test_commands": [
            r"^\s*python3?\s+(-m\s+)?pytest\b",
            r"^\s*pytest\b",
            r"^\s*python3?\s+-m\s+unittest\b",
            r"^\s*tox\b",
        ],
    },
    "javascript": {
        "test_file_patterns": [
            "*.test.js", "*.spec.js", "*.test.jsx", "*.spec.jsx",
            "*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx",
            "**/__tests__/**/*.js", "**/__tests__/**/*.ts",
        ],
        "source_file_patterns": ["*.js", "*.jsx", "*.ts", "*.tsx", "src/**/*.js", "src/**/*.ts"],
        "test_commands": [
            r"^\s*npm\s+test\b",
            r"^\s*npx\s+(jest|vitest|mocha|playwright)\b",
            r"^\s*yarn\s+test\b",
        ],
    },
    "java": {
        "test_file_patterns": ["*Test.java", "*Tests.java", "**/src/test/**/*.java"],
        "source_file_patterns": ["*.java", "src/main/**/*.java"],
        "test_commands": [
            r"^\s*mvn\s+test\b",
            r"^\s*gradle\s+test\b",
            r"^\s*(mvnw|\.\\mvnw)\s+test\b",
            r"^\s*(gradlew|\.\\gradlew)\s+test\b",
        ],
    },
    "go": {
        "test_file_patterns": ["*_test.go", "**/*_test.go"],
        "source_file_patterns": ["*.go", "cmd/**/*.go", "internal/**/*.go", "pkg/**/*.go"],
        "test_commands": [r"^\s*go\s+test\b", r"^\s*go\s+vet\b"],
    },
    "rust": {
        "test_file_patterns": ["tests/**/*.rs", "**/tests/**/*.rs"],
        "source_file_patterns": ["*.rs", "src/**/*.rs"],
        "test_commands": [r"^\s*cargo\s+test\b"],
    },
    "ruby": {
        "test_file_patterns": ["spec/**/*_spec.rb", "test/**/*_test.rb", "**/*_spec.rb"],
        "source_file_patterns": ["*.rb", "lib/**/*.rb", "app/**/*.rb"],
        "test_commands": [
            r"^\s*bundle\s+exec\s+rspec\b",
            r"^\s*rspec\b",
            r"^\s*rake\s+test\b",
            r"^\s*rails\s+test\b",
        ],
    },
    "csharp": {
        "test_file_patterns": ["*Tests.cs", "*Test.cs", "**/Tests/**/*.cs", "**/test/**/*.cs"],
        "source_file_patterns": ["*.cs", "**/*.cs"],
        "test_commands": [r"^\s*dotnet\s+test\b", r"^\s*dotnet\s+vstest\b", r"^\s*nunit3?-?console\b"],
    },
    "dotnet": {
        "test_file_patterns": ["*Tests.cs", "*Test.cs", "**/Tests/**/*.cs", "**/test/**/*.cs"],
        "source_file_patterns": ["*.cs", "**/*.cs"],
        "test_commands": [r"^\s*dotnet\s+test\b", r"^\s*dotnet\s+vstest\b", r"^\s*nunit3?-?console\b"],
    },
    "cpp": {
        "test_file_patterns": ["test_*.cpp", "*_test.cpp", "test_*.cc", "*_test.cc", "**/tests/**/*.cpp"],
        "source_file_patterns": ["*.cpp", "*.cc", "*.cxx", "*.h", "*.hpp", "**/*.cpp"],
        "test_commands": [
            r"^\s*ctest\b",
            r"^\s*cmake\b.*(--build\b.*--target\s+test|--build\b.*-t\s+test)",
            r"^\s*make\s+test\b",
        ],
    },
}

_HEADER = """# phase-barrier 配置（由 `python -m anti_shortcut init` 生成，{date}）
# 加载方式：
#   skill = AntiShortcutSkill(workspace=".", config="{output}", user_request=...)
#   python -m anti_shortcut inspect --workspace . --config {output}
# 所有字段均有默认值，可按需删减 / 修改；完整说明见 docs/configuration.md。
"""


def _fmt_list(items: list[str]) -> str:
    """把列表渲染为 YAML 块序列（带缩进）。"""
    return "\n".join(f"  - {item!r}" for item in items)


def render_config(
    *,
    language: str,
    output: str = "config.yaml",
    coverage_threshold: float | None = None,
    hmac_key: str = "",
    audit_url: str = "",
    rules: list[str] | None = None,
) -> str:
    """渲染带注释的 YAML 配置文本。"""
    from datetime import date

    profile = LANGUAGE_PROFILES.get(language, LANGUAGE_PROFILES["python"])
    lines = [
        _HEADER.format(date=date.today().isoformat(), output=output),
        "# ---- 语言适配 ----",
        f'language: "{language}"   # 显式指定语言（自动检测到 {language}，此处固化防止误判）',
        "",
        "# ---- 阶段 1：Spec 设计 ----",
        'spec_file: "spec.md"',
        "spec_sections:",
        _fmt_list(DEFAULT_SPEC_SECTIONS),
        "spec_min_chars: 120",
        "",
        "# ---- 阶段 2：测试用例 ----",
        "test_file_patterns:",
        _fmt_list(profile["test_file_patterns"]),
        "min_test_functions: 2",
        "require_assert_per_test: true",
        "",
        "# ---- 阶段 3：实现代码 ----",
        "source_file_patterns:",
        _fmt_list(profile["source_file_patterns"]),
        "require_implementation: true",
        "",
        "# ---- 阶段 4/5：测试运行 ----",
        "# 测试命令正则（匹配命令前缀），可按语言扩展",
        "test_commands:",
        _fmt_list(profile["test_commands"]),
        "max_test_output_tail: 4000",
    ]
    if coverage_threshold is not None:
        lines += [
            "",
            "# ---- 覆盖率门禁（可选）----",
            f"coverage_threshold: {coverage_threshold}   # 测试输出含覆盖率报告且不低于该百分比",
        ]
    if hmac_key:
        lines += [
            "",
            "# ---- 状态签名（可选，推荐生产启用）----",
            f'state_hmac_key: "{hmac_key}"   # 也可用环境变量 PHASE_BARRIER_HMAC_KEY',
        ]
    if audit_url:
        lines += [
            "",
            "# ---- 审计远程推送（可选）----",
            f'audit_remote_url: "{audit_url}"   # 审计事件异步 POST 到 SIEM / webhook',
        ]
    if rules:
        lines += [
            "",
            "# ---- 内置安全规则包（可选）----",
            "# 可用规则：no_shell_injection / no_path_traversal / no_hardcoded_secrets / require_license_header",
            "rules:",
            _fmt_list(rules),
        ]
    lines += [
        "",
        "# ---- 安全 ----",
        "protect_gate_dir: true",
        "# 是否允许 Agent 在任意阶段写入“其他”类型文件（README、docs 等）",
        "allow_other_files_any_stage: true",
        "",
        "# 更严格的项目可以这样配：",
        "#   min_test_functions: 5",
        "#   spec_sections: [\"## 需求分析\", \"## 设计方案\", \"## 接口定义\", \"## 验收标准\"]",
        "#   rules_options:",
        "#     license_header: \"Copyright (c) 2026 Example Corp.\"",
        "",
        "# ---- 语义级校验（可选，v0.49.0，默认关闭）----",
        "# 结构校验之上的语义增强，启用后不满足即阻止阶段推进（见 docs/semantic-validation.md）",
        "# semantic:",
        "#   requirement_coverage:    # spec 用 REQ-001 声明需求，测试文件用 # REQ-001 关联",
        "#     enabled: true",
        "#     min_coverage: 100",
        "#   mutation_score:          # Python AST 变异测试，防“空测试 / 假断言”",
        "#     enabled: true",
        "#     min_score: 80",
        "#   spec_specificity:        # spec 具体性五维校验，拒绝“278 字套话”式空泛 spec",
        "#     enabled: true",
        "#     min_entities: 5",
        "#     min_signatures: 2",
        "#     min_decision_phrases: 1",
        "#     min_requirement_anchors: 2",
        "#     max_filler_hits: 1",
        "#   test_assertion_quality:  # 断言质量：拒绝 assert True 等纯常数断言",
        "#     enabled: true",
        "",
    ]
    return "\n".join(lines)


def init_config(
    workspace: str | Path,
    *,
    language: str = "",
    output: str = "config.yaml",
    force: bool = False,
    coverage_threshold: float | None = None,
    hmac_key: str = "",
    audit_url: str = "",
    rules: list[str] | None = None,
) -> tuple[Path, str]:
    """生成并写入配置文件；目标已存在且未指定 force 时抛 ValueError。"""
    ws = Path(workspace).resolve()
    lang = language or detect_language(ws)
    out = Path(output)
    if not out.is_absolute():
        out = ws / out
    gate_dir = ws / ".agent_gate"
    try:
        out.resolve().relative_to(gate_dir.resolve())
    except ValueError:
        pass  # 不在门禁目录内，允许
    else:
        raise ValueError(f"不允许把配置文件写入门禁目录: {out}")
    if out.exists() and not force:
        raise FileExistsError(
            f"配置文件已存在: {out}（使用 --force 覆盖，或指定 --output 其他路径）"
        )
    text = render_config(
        language=lang,
        output=str(out.relative_to(ws)) if out.is_relative_to(ws) else str(out),
        coverage_threshold=coverage_threshold,
        hmac_key=hmac_key,
        audit_url=audit_url,
        rules=rules,
    )
    # 生成前先自检：保证 GateConfig 能直接加载
    data = yaml.safe_load(text) or {}
    GateConfig(**{k: v for k, v in data.items() if k in GateConfig.model_fields})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return out, text
