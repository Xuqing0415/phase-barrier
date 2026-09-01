# phase-barrier 配置指南（v0.26.0）

本文档覆盖 phase-barrier 的全部可配置项：名称、类型、默认值、作用与示例。
配置通过 YAML 文件加载（`AntiShortcutSkill(config="xxx.yaml")` / CLI `--config xxx.yaml`），
所有字段均有默认值，可只覆盖需要的部分。

## 加载方式

```python
skill = AntiShortcutSkill(workspace=".", config="config.yaml", user_request="...")
```

```bash
python -m anti_shortcut inspect --workspace . --config config.yaml
```

也可以直接用 `python -m anti_shortcut init` 生成带注释的配置模板：

```bash
python -m anti_shortcut init --with-coverage --rules no_path_traversal,no_shell_injection
# 生成 config.yaml，可用 --language / --output / --force / --hmac-key / --audit-url 调整
```

## 全部配置项

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `workspace` | Path | `.` |  |
| `gate_dir_name` | str | `.agent_gate` |  |
| `state_file_name` | str | `state.json` |  |
| `audit_log_name` | str | `audit.log` |  |
| `spec_file` | str | `spec.md` |  |
| `spec_sections` | list | `PydanticUndefined` |  |
| `spec_min_chars` | int | `120` |  |
| `test_file_patterns` | list | `PydanticUndefined` |  |
| `min_test_functions` | int | `2` |  |
| `require_assert_per_test` | bool | `True` |  |
| `source_file_patterns` | list | `PydanticUndefined` |  |
| `require_implementation` | bool | `True` |  |
| `test_commands` | list | `PydanticUndefined` |  |
| `max_test_output_tail` | int | `4000` |  |
| `coverage_threshold` | Union | `None` / 空 |  |
| `language` | Union | `None` / 空 |  |
| `language_adapter` | Union | `None` / 空 |  |
| `adapter_options` | dict | `PydanticUndefined` |  |
| `state_hmac_key` | Union | `None` / 空 |  |
| `state_hmac_keys` | list | `PydanticUndefined` |  |
| `evidence_signing` | bool | `True` |  |
| `audit_remote_url` | Union | `None` / 空 |  |
| `audit_remote_token` | Union | `None` / 空 |  |
| `audit_remote_timeout` | float | `5.0` |  |
| `audit_remote_batch_size` | int | `50` |  |
| `audit_remote_max_queue` | int | `1000` |  |
| `audit_remote_flush_interval` | float | `5.0` |  |
| `audit_remote_ca_bundle` | Union | `None` / 空 |  |
| `audit_remote_retries` | int | `2` |  |
| `audit_remote_backoff_factor` | float | `0.5` |  |
| `audit_remote_client_cert` | Union | `None` / 空 |  |
| `audit_remote_client_key` | Union | `None` / 空 |  |
| `audit_remote_headers` | dict | `PydanticUndefined` |  |
| `audit_remote_spool_dir` | Union | `None` / 空 |  |
| `protect_gate_dir` | bool | `True` |  |
| `allow_other_files_any_stage` | bool | `True` |  |

> 说明：`rules`（内置安全规则包列表）与 `rules_options`（规则选项，如
> `license_header`）是 v0.26.0 新增字段，见下文「内置安全规则包」。

## 按场景分组

### 基础门禁（阶段证据要求）

```yaml
# 阶段 1：Spec 设计
spec_file: "spec.md"
spec_sections:
  - "## 需求分析"
  - "## 设计方案"
  - "## 接口定义"
spec_min_chars: 120

# 阶段 2：测试用例
test_file_patterns:
  - "test_*.py"
  - "*_test.py"
  - "tests/**/test_*.py"
  - "tests/**/*_test.py"
min_test_functions: 2          # 至少 2 个测试函数（非 Python 按断言关键字数）
require_assert_per_test: true  # 每个测试函数必须含断言，防止空壳测试

# 阶段 3：实现代码
source_file_patterns: ["*.py"]
require_implementation: true

# 阶段 4/5：测试运行
test_commands:
  - '^\s*pytest\b'
  - '^\s*python3?\s+(-m\s+)?pytest\b'
max_test_output_tail: 4000
coverage_threshold: null        # 设为 80.0 后要求测试输出含覆盖率报告且不低于该值
```

### 语言适配（多语言）

```yaml
language: python       # 显式指定：python / javascript / java / go / rust / ruby / csharp / dotnet / cpp
language_adapter: "my_pkg.module:MyAdapter"   # 或自定义适配器导入路径（优先级最高）
adapter_options:
  min_tests: 3         # 传递给适配器的选项（各适配器自行解释）
```

自动检测标志文件：`package.json` → javascript；`pom.xml` / `build.gradle` → java；
`go.mod` → go；`Cargo.toml` → rust；`Gemfile` → ruby；`*.csproj` / `*.sln` → csharp；
`CMakeLists.txt` / `Makefile` / `*.vcxproj` → cpp（v0.26.0）；`pyproject.toml` /
`requirements.txt` / `setup.py` → python。

### 覆盖率门禁

```yaml
coverage_threshold: 90.0
# 支持 pytest-cov（TOTAL 行）、go test -cover、istanbul（jest / vitest --coverage）等
```

### 状态签名与密钥轮换（安全加固）

```yaml
state_hmac_key: "change-me"          # 未设置时回退环境变量 PHASE_BARRIER_HMAC_KEY
state_hmac_keys: []                  # 轮换期仍接受的旧密钥（也支持 PHASE_BARRIER_HMAC_KEYS 环境变量）
evidence_signing: true               # 推进阶段时把证据文件 SHA-256 写入独立清单
```

### 审计推送（SIEM / webhook）

```yaml
audit_remote_url: "https://siem.example.com/ingest"
audit_remote_token: ""               # Authorization: Bearer <token>
audit_remote_timeout: 5.0
audit_remote_batch_size: 50
audit_remote_max_queue: 1000
audit_remote_flush_interval: 5.0
audit_remote_retries: 2
audit_remote_backoff_factor: 0.5
audit_remote_ca_bundle: ""           # 自建 HTTPS 端点的自定义 CA（PEM）
audit_remote_client_cert: ""         # mTLS 客户端证书（PEM）
audit_remote_client_key: ""          # mTLS 客户端私钥（PEM）
audit_remote_headers: {}             # 额外请求头
audit_remote_spool_dir: ""           # 失败事件落盘目录（进程重启后恢复重发）
```

### 内置安全规则包（v0.26.0）

```yaml
rules:
  - no_path_traversal          # 拦截写入越出工作区的路径（.. 逃逸 / 绝对路径逃逸）
  - no_shell_injection         # 拦截 exec 命令中的 ; / && / || / $(...) / 反引号（严格模式）
  - no_hardcoded_secrets       # 拦截写入内容中疑似密码 / API 密钥 / 私钥
  - require_license_header     # 拦截未携带许可证头的源文件写入
rules_options:
  license_header: "Copyright (c) 2026 Example Corp."   # require_license_header 用
```

规则签名与自定义拦截规则一致：`rule(kind, target, config, stage, content=None) -> (bool, str) | None`；
`content` 仅在规则签名接受该参数时传入（写入场景）。

### 门禁目录与文件策略

```yaml
protect_gate_dir: true        # 门禁目录防护提示（生产请配合只读卷挂载）
allow_other_files_any_stage: true  # 是否允许任意阶段写入“其他”类型文件（README、docs 等）
```

## 常见问题

- **如何关闭某道门禁？** 调低 `min_test_functions`、关闭 `require_assert_per_test`、
  去掉 `coverage_threshold`，或自定义 `spec_sections` / `test_file_patterns`。
- **如何适配非 Python 项目？** 设置 `language` 并核对 `test_file_patterns` /
  `source_file_patterns` / `test_commands`；内置适配器覆盖 JS / Java / Go / Rust / Ruby /
  C# / .NET / C++。
- **如何只启用安全规则中的部分？** `rules` 列表按需增删；未列出的规则不生效。
