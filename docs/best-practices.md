# 最佳实践

本页汇总 phase-barrier 在生产与日常使用中的推荐做法，覆盖阶段建模、证据设计、配置、
Agent 集成选型、常见坑，以及已落地的性能与安全加固清单（对应 Roadmap v0.31.0）。

## 阶段建模

默认门禁为 6 个阶段（`0 需求 -> 1 spec -> 2 测试 -> 3 实现 -> 4 运行测试 -> 5 修复 -> 6 交付`），
大多数场景无需改动。需要自定义时注意：

- **阶段数量保持 0-6 或整体平移**：GitHub Action、编排器 SDK、`classify_stage_path` 都依赖
  编号语义（0 = 需求记录、1 = spec、2 = 测试、3 = 实现、4 = 测试运行、6 = 交付）。
- **不要跳过阶段编号**：`advance` 只允许 `current + 1`，这是防跳步的核心；若要放宽，
  优先调整证据校验器而不是改编号规则。
- **为 5（修复）保留语义**：阶段 4 测试失败会进入 5，修复后重新测试通过再进 6；
  不要让 Agent 在阶段 4 直接“标记通过”。

## 证据设计

证据要**机器可校验、不可事后伪造**：

- **spec**：固定章节（`## 需求分析` / `## 设计方案` / `## 接口定义`），内容校验用
  章节匹配即可；若团队有模板，可配置自定义章节列表。
- **测试**：默认用 AST/启发式统计测试函数与断言数量，防止空壳文件。阈值可调
  （`adapter_options.min_test_functions` / `min_assertions`），但建议至少 1 个测试 + 1 个断言。
- **实现**：语法检查（`py_compile` / `node --check` / `php -l` / `g++ -fsyntax-only` 等）
  保证可执行，不保证正确——正确性交给阶段 4/5 的测试运行。
- **测试运行**：`exec` 自动记录命令输出与退出码；覆盖率门禁可配置
  `coverage_threshold`，无法提取覆盖率时应明确报错而非静默通过。

## 配置建议

```yaml
language: python                 # 显式指定，避免自动检测歧义
adapter_options:
  min_test_functions: 2          # 至少 2 个测试函数
  min_assertions: 2              # 至少 2 个断言
coverage_threshold: 90           # 测试运行覆盖率门禁（可选）
rules:                           # 安全规则包（可选）
  - no_path_traversal
  - no_shell_injection
state_hmac_key: ${HMAC_KEY}      # 生产环境用环境变量/Secret 注入
audit_remote_url: ${AUDIT_URL}   # 审计远程推送（SIEM / webhook）
```

- **显式 `language`** 优先于自动检测；多标志文件并存（如同时有 `package.json` 和
  `requirements.txt`）时，自动检测按固定顺序返回，显式配置最稳妥。
- **HMAC 状态签名**（v0.8.0+）默认建议开启：状态文件每次写入携带签名，被篡改即拒绝启动。
- **审计日志**结构化 JSON，可推送到远程端点；K8s 场景通过 Secret 注入环境变量。

## Agent 集成选型

| 场景 | 推荐方式 |
|------|----------|
| 平台型 Agent（自己能改工具注册表） | SDK `PhaseBarrier`（`examples/orchestrator_hooks/`） |
| 第三方 Agent 容器 / K8s | sidecar 透明代理 + `GateClient`（`/api/write` `/api/exec`） |
| CI 门禁 | GitHub Action `inspect` / `verify` 模式 |
| 人工监督 / 脚本 | CLI `inspect` / `advance` / `exec` / `verify-evidence` |

集成优先级：**先 SDK（最小侵入）-> 再工具透明代理（不可绕过）-> 最后 CI 兜底**。

## 常见坑

- **空壳测试文件**：只检查文件存在不够，务必启用测试有效性校验（函数数 + 断言数）。
- **永远通过的假测试**：门禁只保证“跑过”，不保证“测了”。用覆盖率阈值 + 人工抽查辅助。
- **文件模式误判**：测试文件命名约定（`test_*.py` / `*.test.js` / `*Test.java`）要与你
  的项目一致，否则源文件可能被当成测试文件放行。
- **命令注入**：`execute_command` 中 `;` / `&&` / `|` / `$()` 等要按规则拦截
  （内置 `no_shell_injection` 规则），不要把测试命令识别写成前缀宽松匹配。
- **状态目录暴露**：`.agent_gate/` 必须对 Agent 只读；sidecar 模式下用独立卷隔离
  （`gate-state`），Agent 连读都拿不到写路径。
- **Windows 端口占用**：本地跑 sidecar 测试时 `0.0.0.0:8080` 可能被占用/受限，
  CI 使用 ubuntu runner 无此问题。

## 性能与安全加固清单（v0.31.0 起已落地）

对应 Roadmap v0.31.0 已落地的“性能与安全加固（解析器模糊测试 / 依赖漏洞扫描）”：

- **多 Agent 并发**：状态文件已支持跨进程文件锁（POSIX `flock` / Windows `msvcrt.locking`）
  与原子替换（temp + rename）；基准脚本 `benchmarks/bench.py` 默认 100 线程 × 20 轮，
  CI `bench` job 以 p95 阈值做回归门禁。
- **依赖漏洞扫描**：已纳入 CI——`.github/workflows/security.yml` 每周一 06:00 UTC + 每次
  push / PR 触发，`pip-audit` 审计完整安装环境 + `osv-scanner` 扫描 `pyproject.toml`
  （v0.31.0）；发布前仍可额外跑 `pip-audit -r requirements` 双保险。
- **模糊测试**：已脚本化——`benchmarks/fuzz_parsers.py` 对 8 个解析 / 识别纯函数做
  确定性模糊测试（固定种子可复现），CI `bench` job 以 `--fail-fast --iterations 1000`
  门禁（v0.31.0）；新语言适配器请同步补边界用例。
- **供应链**：发布已启用 PyPI trusted publishing（OIDC），Release 附带 sigstore 签名与
  attestation；验证命令：

  ```bash
  gh release view v0.45.1 --json assets   # 查看 .sigstore.json / .publish.attestation
  ```