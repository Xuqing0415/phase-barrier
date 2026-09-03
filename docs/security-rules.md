# 拦截规则与安全加固

> 迁移自 README 精简版主页；相关：[配置指南](configuration.md)、[K8s 部署](k8s.md)。[返回 README](https://github.com/Xuqing0415/phase-barrier#readme)

## 工具拦截规则

| 工具 / 命令 | 拦截条件 | 提示 |
|-------------|----------|------|
| `write_file` 写实现代码 | 阶段 < 2（测试未完成） | 请先完成测试用例编写 |
| `write_file` 写测试文件 | 阶段 < 1（spec 未完成） | 请先完成 spec 设计 |
| `write_file` 写 `.agent_gate/` | 任意阶段 | 门禁目录由 Skill 独占 |
| `execute_command` 运行测试（pytest 等） | 阶段 < 3（实现未完成） | 请先完成实现代码 |
| shell 写入源码/测试（`>`、`sed -i`、`mv`、`rm`、`touch`…） | 同上按文件类型 | 与 `write_file` 相同的阶段限制 |
| 任何访问 `.agent_gate` 的命令 | 任意阶段 | 禁止通过 shell 访问门禁目录 |
| 只读命令（`ls` / `cat` / `dir` …） | 无 | 放行 |

`advance_stage(new_stage)` 是唯一合法的阶段推进入口：

- `new_stage` 必须等于当前阶段 + 1，否则返回“不允许跳跃阶段”；
- 推进前运行**当前阶段**的证据校验器，不通过则返回详细失败原因；
- 通过后写入状态机（原子写：临时文件 + `os.replace`），并记录证据哈希。

### 自定义校验器与拦截规则（v0.12.0）

阶段校验器与工具拦截规则都支持扩展，优先级：自定义（进程内 + 入口点）> 内置：

- **自定义校验器**：`register_validator(stage, fn)` 进程内注册，或通过 `phase_barrier.validators` 入口点组加载
  （`{stage: fn}` 映射 / 带 `stage` 属性的单校验器 / 返回映射的工厂三种形式），覆盖对应阶段的证据校验；
  校验器签名 `fn(workspace, config, state, adapter=None) -> (ok, message, evidence)`。
- **自定义拦截规则**：`register_rule(name, rule)` 进程内注册，或通过 `phase_barrier.interceptors` 入口点组加载
  （规则函数 / 返回规则列表的工厂 / `{name: rule}` 映射 / 带 `rules` 属性对象四种形式）；
  规则签名 `rule(kind, target, config, stage) -> (False, reason) 拦截 / (True, reason) 放行 / None 弃权`，
  在 `write` / `exec` 内置检查之前评估，首个决定性结论生效（异常规则自动跳过）。

第三方包在 `pyproject.toml` 声明入口点即可参与门禁：

```toml
[project.entry-points."phase_barrier.validators"]
strict_tests = "my_plugins:strict_tests_validator"

[project.entry-points."phase_barrier.interceptors"]
deny_vendor = "my_plugins:deny_vendor_rule"
```

**可运行示例**：`examples/plugin_rules/` 提供完整插件包（自定义校验器 + 拦截规则 +
`pyproject.toml` 入口点声明 + demo）：

```bash
python examples/plugin_rules/demo.py                    # 进程内注册（零安装）
pip install -e examples/plugin_rules                    # 安装示例插件包
python examples/plugin_rules/demo.py --via-entry-point  # 经入口点加载
```

校验器函数声明所属阶段后即可被入口点识别：

```python
def require_design_review(workspace, config, state, adapter=None):
    if not (workspace / "design-review.md").exists():
        return False, "缺少 design-review.md", {}
    return True, "design-review 已提供", {}

require_design_review.stage = 1   # 注册到阶段 1，覆盖内置 spec 校验
```

## 安全与防绕过

- **状态文件保护**：Agent 可用的工具全部被包装；`.agent_gate` 的任何写入 / shell 访问都被拦截。
- **生产部署**：将 `.agent_gate` 以只读卷挂载给 Agent 执行容器（`-v /host/path:/.agent_gate:ro`），实现进程级隔离——即使 Agent 绕过工具包装也无法篡改状态。
- **空壳防护**：测试文件经 AST 分析，要求测试函数数量 ≥ 阈值且每个函数含断言（`assert` / `pytest.raises`）。
- **伪造防护**：证据文件在推进时记录 SHA-256；阶段 5 要求测试运行发生在最后一次代码修改之后。
- **状态签名（v0.8.0）**：配置 HMAC 密钥后，状态文件每次写入都带 `signature` 字段并在加载时校验；篡改 / 未签名 / 密钥不匹配 -> `TamperedStateError`，CLI 明确报错并拒绝运行。
- **证据签名（v0.9.0）**：独立于 state.json 的证据清单记录每个阶段推进时的文件 SHA-256；即使 Agent 伪造状态文件，`verify-evidence` 仍能发现证据被替换 / 缺失。
- **密钥轮换（v0.9.0）**：`rotate-key` 校验现有签名后以新密钥重新签名；`state_hmac_keys` / `PHASE_BARRIER_HMAC_KEYS` 提供宽限期双密钥，轮换不中断。
- **审计远程推送（v0.9.0）**：`audit_remote_url` 把审计事件异步转发到 SIEM；队列有界、失败只计数，不影响门禁执行。
- **审计可靠性（v0.10.0）**：发送失败按指数退避自动重试；自定义 CA 支持内网 / 自建 SIEM 的 HTTPS 端点。
- **审计传输安全（v0.11.0）**：mTLS 客户端证书（`audit_remote_client_cert` / `audit_remote_client_key`）
  支持双向 TLS；`audit_remote_headers` 携带自定义请求头；`audit_remote_spool_dir` 把重试耗尽的事件
  落盘为 JSONL，进程重启自动恢复重发（适合 K8s 滚动重启 / 崩溃场景）。
- **证据 Git 门禁（v0.11.0）**：`verify-evidence --git-base <ref>` 用 `git diff --name-only` 列出本次
  变更文件，与证据清单条目求交集——证据文件被本次提交修改即失败，供 CI 强制“证据不可事后篡改”。
- **日志审计**：所有拦截与阶段变更写入 JSON 审计日志，便于事后分析“哪些请求被拦截”“跳过步骤的频率”。

## Docker 只读卷部署（进程级防绕过）

即使 Agent 绕过工具包装直接操作文件系统，也可通过“只读挂载”从文件系统层面锁死 `.agent_gate`：

```bash
docker compose -f deploy/docker-compose.yml up --build
```

- `gate-keeper` 服务：对 `/workspace/.agent_gate` **可写**，负责初始化状态并跑完整门禁流程；
- `agent` 服务：`/workspace` 可写（产出代码），但 `/workspace/.agent_gate` **只读挂载**（`:ro`）；
- `agent` 侧探针验证：读状态正常、写门禁目录被拒绝（`PermissionError`）、写工作区源码正常。

详见 [`deploy/README.md`](https://github.com/Xuqing0415/phase-barrier/blob/main/deploy/README.md)。
Kubernetes 版（v0.7.0）见 [`deploy/k8s/README.md`](https://github.com/Xuqing0415/phase-barrier/blob/main/deploy/k8s/README.md)：
Job `gate-keeper` 初始化门禁状态卷，`agent + gate-sidecar` 共享工作区卷，
sidecar 独占挂载门禁目录并暴露 HTTP API（`anti_shortcut.sidecar`），
Agent 只能通过 sidecar 查询 / 推进阶段。 v0.17.0 起可把文件写入与命令执行也交给 sidecar（`POST /api/write`、`POST /api/exec`），门禁下沉到文件系统层。
