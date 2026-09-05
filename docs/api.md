# API 参考

phase-barrier 的 Python 包名为 `anti_shortcut`（发行名 / CLI 见下），核心 API 面向两类调用方：
宿主进程内嵌（`AntiShortcutSkill` + 包装工具）与 sidecar 进程外代理（`GateClient` HTTP / gRPC API）。

安装：

```bash
pip install phase-barrier        # import anti_shortcut
```

- 发行名：`phase-barrier`（PyPI）
- 命令行：`anti-shortcut` / `python -m anti_shortcut`（详见 [CLI 使用](usage.md)）
- 配置模型：`anti_shortcut.config.GateConfig`（YAML / Pydantic，字段全表见 [配置指南](configuration.md)）

## 核心门禁

| 符号 | 说明 |
|------|------|
| `anti_shortcut.skill.AntiShortcutSkill` | 阶段门禁核心：初始化状态机、包装工具、阶段推进/校验 |
| `anti_shortcut.skill.make_gated_tools` | 包装 `write_file` / `execute_command` 并注入 `advance_stage` |
| `anti_shortcut.state.StateManager` | 状态机持久化（原子写入 + HMAC 签名 + 文件锁） |
| `anti_shortcut.validators` | 阶段证据校验器（spec / 测试 / 实现 / 测试运行 / 覆盖率） |
| `anti_shortcut.interceptors` | 工具拦截器与命令/路径识别（`is_test_command`、`touches_gate_dir` 等） |
| `anti_shortcut.evidence` | 证据清单（SHA-256）与 git 增量校验 |
| `anti_shortcut.audit` | 结构化审计日志 |
| `anti_shortcut.remote_audit.RemoteAuditSink` | 审计远程推送（SIEM / webhook，mTLS + 重试 + 本地 spool） |
| `anti_shortcut.rules` | 内置安全拦截规则包（shell 注入 / 路径穿越 / 硬编码密钥等） |
| `anti_shortcut.plugins.verify_plugins` | 入口点插件自动验证（`plugin-verify`） |


## 语义级校验（v0.50.0）

| 符号 | 说明 |
|------|------|
| `anti_shortcut.semantic.SemanticValidator` | 语义校验器抽象基类（`name` / `stages` / `check(workspace, config, state, adapter=None) -> SemanticCheckResult`） |
| `anti_shortcut.semantic.SemanticCheckResult` | 单次检查结果（`ok` / `message` / `evidence`） |
| `anti_shortcut.semantic.RequirementCoverageValidator` | 需求追踪：spec `REQ-001` -> 测试 `# REQ-001` 引用（阶段 2） |
| `anti_shortcut.semantic.MutationScoreValidator` | Python AST 变异测试：存活变异体过多即测试质量不足（阶段 4） |
| `anti_shortcut.semantic.SpecSpecificityValidator` | spec 具体性五维校验：实体 / 签名 / 技术决策 / 需求锚点 / 套话句式（阶段 1） |
| `anti_shortcut.semantic.TestAssertionQualityValidator` | 测试断言质量：拒绝 `assert True` 等纯常数断言，仅 Python（阶段 2） |
| `anti_shortcut.semantic.extract_request_anchors` / `extract_concrete_entities` / `extract_interface_signatures` | spec 具体性提取辅助（需求锚点 / 具体实体 / 接口签名，供自定义校验器复用） |
| `anti_shortcut.semantic.analyze_spec_specificity` / `analyze_test_assertion_quality` | 五维分析 / 断言质量分析入口（返回结构化明细） |
| `anti_shortcut.semantic.register_semantic_validator` / `load_semantic_plugins` | 进程内注册 / 加载入口点语义校验器（`phase_barrier.semantic_validators`） |
| `anti_shortcut.semantic.run_semantic_checks` | 汇总执行启用的语义校验器，任一失败即阻止推进；返回 `(ok, message, evidence)` |
| `anti_shortcut.semantic.extract_requirement_ids` / `extract_test_references` | REQ 条目 / 测试引用提取（供自定义校验器复用） |
| `anti_shortcut.semantic.generate_mutations` / `run_mutation_suite` | 变异体生成（确定性种子）与批量执行统计（killed / survived / error） |

完整用法与配置见 [语义级校验](semantic-validation.md)。

## 语言适配层

| 符号 | 说明 |
|------|------|
| `anti_shortcut.languages.LanguageAdapter` | 语言适配器抽象基类 |
| `anti_shortcut.languages.LANGUAGE_REGISTRY` | 内置适配器注册表 |
| `anti_shortcut.languages.detect_language` / `get_adapter` | 自动检测与适配器选择（配置 > 检测 > 默认 Python） |
| `anti_shortcut.languages.python.PythonAdapter` 等 | 内置适配器：python / javascript / java / kotlin / scala / swift / dart / go / rust / ruby / php / cpp / csharp / dotnet |

## Sidecar / 代理 / SDK

| 符号 | 说明 |
|------|------|
| `anti_shortcut.sidecar.GateSidecar` / `make_handler` | 进程外 HTTP 门禁服务（state / advance / write / exec / audit） |
| `anti_shortcut.grpc_service` | gRPC 门禁服务（v0.39.0）：8 个 RPC 与 HTTP API 等价；`python -m anti_shortcut.grpc_service` 独立启动 |
| `anti_shortcut.proto.sidecar_pb2` / `sidecar_pb2_grpc` | gRPC 生成代码（`sidecar.proto`，随包分发；可选依赖 `phase-barrier[grpc]`） |
| `anti_shortcut.proxy_client.GateClient` | sidecar 客户端（零额外依赖，`urllib`）；被拦截抛 `GateDenied` |
| `anti_shortcut.sdk.PhaseBarrier` | 编排器钩子 SDK：`check(stage)` / `advance(to_stage)` / `record_test_run` / `verify_evidence` / `list_stages` / `stage_of` 及辅助查询（见下） |
| `anti_shortcut.proxy` | K8s 透明代理辅助（写 / 执行代理） |
| `anti_shortcut.init` | `anti_shortcut init`：自动生成带注释的项目配置 |

## 工具 / 入口

| 命令 | 说明 |
|------|------|
| `anti-shortcut init` | 生成配置模板（`--language` / `--output` / `--force`） |
| `anti-shortcut inspect` | 查看当前阶段与证据（`--json`） |
| `anti-shortcut write` / `exec` | 经门禁写文件 / 执行命令（CLI 透明代理） |
| `anti-shortcut advance` | 声明完成当前阶段并推进（校验证据） |
| `anti-shortcut sidecar` | 启动 HTTP 门禁服务（`--port` / `--tls-*` mTLS） |
| `python -m anti_shortcut.grpc_service` | 启动 gRPC 门禁服务（v0.39.0；`--port` / `--tls-*` mTLS） |
| `anti-shortcut query` | 编排器辅助查询：证据库存 / 最近测试 / 阶段历史 / git 变更（v0.45.1，`--json`） |
| `anti-shortcut plugin-verify` | 插件自动验证 |
| `anti-shortcut verify-evidence` / `export-evidence` | 证据清单校验 / 导出 |
| `anti-shortcut rotate-key` | HMAC 密钥轮换 |

各模块的完整函数签名以源码 docstring 为准：`python -c "import anti_shortcut.skill, inspect; print(inspect.getsource(anti_shortcut.skill))"`。
