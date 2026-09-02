# CLI 使用

`python -m anti_shortcut`（脚本入口 `anti-shortcut`）提供以下子命令：

| 子命令 | 说明 |
|--------|------|
| `plugin-verify` | 验证当前环境全部插件入口点（v0.29.0，供插件 CI 使用） |
| `init` | 生成 phase-barrier 配置模板（v0.26.0，自动检测语言） |
| `inspect` | 查看当前门禁状态（`--json` 输出结构化结果） |
| `advance` | 推进阶段（`--to N` 必须等于当前阶段 + 1，校验当前阶段证据） |
| `check` | 只读校验是否放行进入指定阶段（v0.22.0） |
| `verify-evidence` | 对照工作区校验证据签名清单（`--git-base` 支持 Git 基线门禁） |
| `export-evidence` | 把证据清单 + 文件哈希导出为可审计 bundle |
| `rotate-key` | 轮换状态签名 HMAC 密钥（支持从无签名状态启用） |
| `write` | 经门禁写入工作区文件（v0.18.0，受阶段与规则约束） |
| `exec` | 经门禁执行 shell 命令（v0.18.0，自动记录测试结果） |
| `sidecar` | 运行 sidecar 门禁 HTTP 服务（v0.20.0，支持 mTLS） |

## 常用示例

```bash
# 插件验证（返回码 0 = 全部通过）
python -m anti_shortcut plugin-verify
python -m anti_shortcut plugin-verify --json

# 状态检查
python -m anti_shortcut inspect --workspace . --json

# 推进到阶段 2（spec 证据校验）
python -m anti_shortcut advance --to 2 --workspace . --config config.yaml

# 门禁执行测试命令
python -m anti_shortcut exec --command "pytest -q" --workspace .

# 导出证据 bundle
python -m anti_shortcut export-evidence --workspace . --out evidence-bundle.json
```

## 退出码

- `0`：命令成功 / 门禁通过
- `1`：校验失败 / 阶段被拒绝 / 配置或证据错误
- 其他：见具体命令输出

## 门禁阶段

| 阶段 | 名称 | 必需证据 |
|------|------|----------|
| 0 | 需求接收 | 用户需求原文（系统传入） |
| 1 | Spec 设计 | `spec.md`，含必需章节 |
| 2 | 测试用例编写 | 测试代码文件（AST 校验测试函数 + 断言） |
| 3 | 实现代码 | 源代码文件（语法检查通过） |
| 4 | 运行测试 | 测试命令退出码 0 + 结果摘要 |
| 5 | 修复与回归 | 修改后源码 + 再次测试全部通过 |
| 6 | 交付 | 交付摘要或用户确认 |

完整配置项与阶段定制见 [配置指南](configuration.md)。