# SymPy pilot 零拦截复核

> 本页仅审计 pilot，不能套用于正式批。2026-11-14 正式批三个 SymPy 已分别归为
> `zero_interception_suspicious`：阶段 2 的复合 shell pytest 命令漏识别，stdout/trace 对账无漏写。
> SymPy-18211 正式双组长耗时跨宿主机睡眠，已排除主要成本；见 [正式批审计](verified20-formal-gate-audit.md)。

对象为 `sympy__sympy-18211` 原 pilot gated：35 轮、2,187 字符、0 次拦截，
官方 resolved，门禁阶段 6 且 `delivery_clean=1`。

## 归类与适用范围

归类为 **`zero_interception_benign`（按保留的可观察轨迹解释）**：零拦截表示这次运行
没有记录到工具或阶段拒绝，不表示门禁未参与。原日志与最终补丁的顺序相符：

| 轮次 | 可观察行为 |
|------|------------|
| 23–24 | 写入 spec，成功进入阶段 2 |
| 25–28 | 写入 3 个复现测试，先观察失败，再成功进入阶段 3 |
| 29–31 | 修改 `sympy/core/relational.py`，查看 diff，再进入阶段 4 |
| 32–34 | 复现测试及既有测试执行；早期命令错误后继续测试 |
| 35 | 最近测试通过，从阶段 4 合法跳转至阶段 6，结束运行 |

最终补丁修改实现并新增 `test_pb_repro.py`，未修改既有测试文件。
4→6 是状态机支持的路径，无需人为经过阶段 5。没有观察到修改门禁状态、伪造测试
或交付后改码的具体证据，因此撤销“仅因零拦截且缺少新证据而待查”的启动阻断。

**证据限制仍保留**：原 runner 没有保存完整命令参数或 state/trace，不能逐事件证明
原运行不存在未观察行为，也不能逐时间戳证明旧交付新鲜度。benign 是对现有零拦截
观察的归类，不是完整历史无绕过证明；新产物不回填或覆盖原产物。

## 修复后的独立复核

新输出位于 `.pytest_tmp/verified20/audit_revalidation/`，不并入正式统计。

- SymPy：36 轮、1,761 字符、阶段 6、`gate_completed=1`；完整 trace 52 个事件，
  其中 1 个 `blocked=true`，与 stats 一致。拒绝发生在阶段 1 的 shell 命令，
  不是将一个拒绝重复计算；4→6 的最终交付时间戳核验通过。
- 官方 resolved：FAIL_TO_PASS 1/1，PASS_TO_PASS 54/54，note=graded。
- Astropy：51 个事件、3 次阶段拒绝，与 stats/state 一致，交付新鲜度核验通过。
  其官方 unresolved 的测试覆盖差异另见 [Astropy 审计](verified20-astropy-audit.md)。
- 另有 Astropy、Django 两个真实镜像的确定性验证：各 3 个事件 / 2 次拒绝，
  验证工具拒绝、阶段拒绝和持久化记录；没有模型请求。

新 SymPy 运行出现 1 次拒绝，不是原运行 0 次拒绝的反证，也不能将原计数改为 1。
它与确定性验证共同支持修复后计数器和持久化链路可用；原 pilot 的行为判断仍以
原日志和补丁为依据。

## 中断与评分恢复

原复核 runner PID 20024 消失，状态文件仍为 running；两只遗留评分容器只剩空闲进程。
已归档状态、启动记录和日志，标记 stale；中断原因未知，不归因于门禁或 Docker 崩溃。
恢复时只移除这两只孤立复核容器，对保存的相同补丁重新执行官方评分，使用独立
`recovery` run ID；没有重新调用模型，没有覆盖中断评分日志，没有重试至 resolved。
两项最终 note 均为 graded，Astropy 的 unresolved 仍原样保留。
