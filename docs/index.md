# phase-barrier

强制编码 Agent（如 Alpha-SWE）遵循标准工程师 SOP 的 **阶段门禁（Stage Gate）** 组件：
以“阶段状态机 + 证据校验 + 工具拦截”的组合，阻止 Agent 跳步、偷步或伪造产出。

> 流程：**需求 → spec 设计 → 测试用例 → 实现 → 测试 → 修复 → 交付**

[![CI](https://github.com/Xuqing0415/phase-barrier/actions/workflows/ci.yml/badge.svg)](https://github.com/Xuqing0415/phase-barrier/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/phase-barrier.svg)](https://pypi.org/project/phase-barrier/)
[![Python versions](https://img.shields.io/pypi/pyversions/phase-barrier.svg)](https://pypi.org/project/phase-barrier/)
[![Marketplace](https://img.shields.io/badge/Marketplace-Phase%20Barrier%20Gate-blue.svg?logo=github&logoColor=white)](https://github.com/marketplace/actions/phase-barrier-gate)

## 特性

- **不可绕过**：校验逻辑位于 Agent 工具调用层，Agent 无法通过自然语言指令绕过；状态文件由 Skill 独占原子写入。
- **最小侵入**：通过包装 `write_file` / `execute_command` 实现拦截，不改变核心工具接口。
- **证据明确**：每个阶段要求具体可验证的产物（文件、AST 统计、测试退出码与摘要）。
- **自动校验**：spec 章节检查、测试 AST 分析（函数数量 + 断言）、实现语法检查、测试结果解析，全部自动完成。
- **可配置**：YAML + Pydantic 配置，可自定义阶段要求、文件模式、测试命令，或关闭某些严格校验。
- **多语言（v0.3.0+）**：Python / JavaScript/TypeScript / Java / Go / Rust / Ruby / C# / C++ / PHP / .NET 语言适配器，可插拔扩展。
- **K8s sidecar（v0.7.0）**：Agent 容器不挂载门禁目录，通过 sidecar HTTP 代理写入与执行，无法绕过门禁。
- **状态签名（v0.8.0）**：HMAC-SHA256 签名 state.json，篡改即拒绝启动。
- **供应链签名（v0.10.0）**：发布流程用 sigstore 对 sdist / wheel 签名。
- **GitHub Action**：`phase-barrier-gate` 已上架 Marketplace，CI 中强制 Agent 产出按 SOP 推进。
- **插件生态（v0.29.0）**：`plugin-verify` 自动验证语言适配器 / 校验器 / 拦截规则 / 集成插件入口点，插件 CI 模板一键接入。

## 快速上手

```bash
pip install phase-barrier

# 生成配置模板（自动检测语言）
python -m anti_shortcut init

# 一键体验（Docker，无需安装依赖）
docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo
```

详细步骤见 [快速开始](quickstart.md)。