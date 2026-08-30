# K8s sidecar 透明代理示例（v0.17.0）

最小可运行的 Agent 接入示例：展示 Agent 把 `write_file` / `execute_command` 重定向到
sidecar 的 `/api/write` / `/api/exec` 后，如何被阶段门禁拦截、以及按 SOP 如何全部通过。

## 运行

```bash
python examples/k8s_proxy/demo.py
```

预期输出（示意）：

```
[demo] 1) Agent 图快：未写 spec 直接写实现 -> 应被拦截
       拦截成功: 当前阶段不允许编写实现代码：请先完成测试用例编写（阶段2）并通过 advance_stage 校验
[demo] 2) Agent 图快：未写实现直接跑 pytest -> 应被拦截
       拦截成功: 当前阶段不允许运行测试命令：请先完成实现代码（阶段3）并通过 advance_stage 校验
[demo] 3) 按 SOP 推进：spec -> 测试 -> 实现
       已进入阶段: 测试用例编写
       ...
[demo] 5) 测试通过 -> 推进 -> 直接交付
       当前阶段: 交付 | 完成: True
```

## 代码结构

- `demo.py`：自包含脚本——进程内启动 sidecar HTTP 服务，用 `GateClient` 模拟 Agent 循环。
- 客户端 API（`anti_shortcut.proxy_client.GateClient`）：
  - `write_file(path, content)`：经门禁写入工作区文件，被拒抛 `GateDenied`。
  - `execute_command(command, timeout=None)`：经门禁执行 shell 命令并自动记录测试结果。
  - `advance(new_stage)` / `record_test_run(...)` / `record_source_change(...)`：原有门禁 API。

## 真实 K8s 接入

1. 用 `deploy/k8s/gate-sidecar.yaml` 部署 `agent + gate-sidecar` 双容器 Pod。
2. Agent 侧把工具实现改为走 `GateClient`（仅标准库 `urllib`，无第三方依赖）：
   ```python
   from anti_shortcut.proxy_client import GateClient

   gate = GateClient("http://localhost:8080")  # sidecar 容器端口
   gate.write_file("spec.md", spec_text)
   gate.execute_command("python -m pytest -q")
   ```
3. 即使 Agent 绕开自身工具包装直接改文件，也无法触碰 `.agent_gate`（sidecar 独占挂载），
   且 `/api/write` 与 `/api/exec` 是文件系统层唯一的“受信通道”，阶段门禁不可绕过。