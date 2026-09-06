# 语义级校验（v0.51.0）

内置阶段校验器检查的是“结构 / 形式”证据：章节存在、测试函数与断言、语法、
测试退出码、覆盖率。这些证据理论上可以被“形式上完整但内容空泛”的产物绕过——
空泛 spec、无断言测试、永远通过的测试。**语义级校验**把“需求 ↔ 测试 ↔ 实现”
的关联质量纳入阶段推进门禁，是结构校验之上的可选增强层。

> 默认全部关闭（`semantic.*.enabled: false`），不影响既有门禁行为；启用后不满足
> 即阻止阶段推进，并把可操作的失败原因返回给 Agent。

## 校验层级回顾

| 层级 | 手段 | 能防住什么 |
|------|------|-----------|
| 文件存在性 | 检查 `spec.md` / `test_*.py` 是否存在 | 最基本门槛 |
| 格式 / 结构 | 检查标题、章节、函数名、注释 | 模板化生成可绕过 |
| 语法 / 编译 | `py_compile` / `javac` / `tsc` | 语法错误 |
| 静态分析 | AST 统计测试函数与断言 | 无断言的测试 |
| 动态执行 | 运行测试命令并检查退出码 | 空测试 / 假断言 |
| 覆盖率 | 提取 coverage 并设阈值 | 覆盖率高但无断言有效性的空转测试 |
| **语义增强（v0.51.0）** | **需求追踪 + 变异测试 + spec 具体性 + 断言质量 + 实现追踪** | **spec 与测试脱节、测试不敏感于实现行为、套话 spec、空壳测试、spec 与实现脱节** |

## 内置校验器一：需求追踪（requirement_coverage）

让 spec 的需求条目与测试建立**显式关联**，防止“spec 写得很好，测试却与需求无关”。

1. 在 `spec.md` 中以 `REQ-001` 形式声明需求条目（编号 1–6 位数字）：

   ```markdown
   ## 需求分析
   - REQ-001: 支持用户名密码登录，凭据正确返回 True
   - REQ-002: 密码错误时返回 False 且不抛异常
   ```

2. 在测试文件中用注释显式关联（大小写与空白容错）：

   ```python
   # REQ-001
   def test_login_ok():
       assert login("a", "b") is True
   ```

3. 在“测试用例编写”阶段推进（阶段 2 → 3）时校验：每个 REQ 必须被至少一个
   测试文件引用，默认要求覆盖率 100%（`min_coverage` 可下调）。

失败消息示例：

```text
需求追踪未通过：以下需求没有任何测试引用：REQ-002（覆盖率 50.0% < 100.0%）。
请在对应测试文件中加注释关联，如 `# REQ-001`
```

spec 未声明任何 REQ 时该校验自动跳过（不强制所有项目使用编号需求）。

## 内置校验器二：变异测试（mutation_score）

对实现源码做确定性 AST 变异（运算符 / 比较 / 布尔翻转、`not` 删除），用工作区
现有测试集逐个运行变异体：

- **killed**：变异被测试捕获（测试失败）；
- **survived**：测试全部通过（测试没有捕获该变异，说明测试对行为不敏感）；
- **error**：超时 / 无法运行，不计入分数。

突变分数 = killed / (killed + survived)。默认要求 ≥ 80%；低于阈值即拒绝，防止
“空测试 / 假断言”通过门禁：

```text
变异测试未通过：突变分数 40.0% < 80.0%（killed 2 / survived 3）。
测试未捕获这些变异，请补充真实断言或修正测试逻辑
```

限制与提示：

- 变异测试目前仅支持 Python 项目（其他语言自动跳过，并给出说明）；
- 需最近一次测试运行通过、且工作区存在可变异源码与测试文件，否则跳过；
- 每个变异体在独立临时副本中运行，耗时随变异体数量与测试规模增长——
  建议先本地试跑再在 CI / 门禁中启用，可调低 `max_mutants` 或调大 `timeout_per_mutant`。

## 内置校验器三：spec 具体性（spec_specificity）

结构校验只要求 spec 存在且含必需章节——“278 字量级套话 spec”也能通过。该校验在
阶段 1 推进时对 spec 做五维分析，任一维度不达标即阻止：

- **具体实体**：命名函数 / 类 / 赋值键 / API 路径 / 反引号代码（如 `fib`、
  `/api/v1/login`、`token=`），默认至少 5 个；
- **接口签名**：def 行 / 函数·输入·输出·参数·返回·异常列表项 / HTTP 端点，
  默认至少 2 条；
- **明确技术决策**：“采用 X 而非 Y / 避免 Z”式表述，默认至少 1 条；
- **需求锚点命中**：从用户原始需求提取 latin 标识符与中文双字领域词（过滤
  “实现 / 函数 / 模块”等泛指词），核对 spec 是否逐条回应，默认至少命中 2 个；
- **套话句式上限**：命中“采用合适的技术方案 / 提供完整接口 / 满足全部需求”等
  默认 8 条空话正则，默认最多允许 1 次。

失败消息按维度给出当前值与下限，并提示“在 spec 中命名具体函数 / 接口 /
数据结构，给出明确技术选型与理由，并逐条回应原始需求”。用户原始需求缺失时
需求锚点子检查自动关闭。

## 内置校验器四：测试断言质量（test_assertion_quality）

结构校验只要求测试函数含 `assert` 语句——“`assert True` / `assert 1 == 1`”
这类不引用被测代码的“纯常数断言”同样能通过。该校验在阶段 2 推进时对 Python
测试文件做 AST 分析：若某 test 函数的断言全部为纯常数断言（不引用任何名称 /
属性 / 调用 / 下标，即不来自被测代码的变量或返回值），则标记为弱函数。

- `strict: true`（默认）：任一弱函数即拒绝，消息直接列出 `文件:函数` 与行号；
- `strict: false`：只警告不拦截；
- `min_assert_targets`（v0.51.0，默认 0 关闭）：开启后每个 test 函数必须覆盖至少
  N 个不同“断言目标”（根标识符 / 被调函数；属性链只计根对象，如 `user.age` 计
  `user`），低于阈值按 `too_few_targets` 拒绝并给出目标清单；
- `require_meaningful_names`（默认 false 关闭）：开启后 test 函数名需含
  行为动词（`should` / `returns` / `raises` 等 allowlist）或 spec 承诺的
  功能关键词之一，否则按 `generic_name` 拒绝——拦截 `test_1` / `test_a` /
  `test_one` 式占位命名；仅对含断言的 test 函数生效。动词清单为保守
  allowlist（宁可漏报不强报），启用前建议先在小范围试点；
- 仅支持 Python（其他语言自动跳过）；语法错误文件跳过不误伤。

## 内置校验器五：实现-文档双向追踪（implementation_traceability）

结构校验只要求“有实现文件且语法正确”——“spec 承诺 `login` / `/api/v1/login`，
代码却实现别的函数”同样能通过。该校验在阶段 3 推进时（仅 Python）把 spec 承诺的
具体实体与实现源码做双向核对：

- **正向（拦截）**：spec 中的函数 / 类 / 字段标识符与 REST 端点路径，须在任一
  实现文件中以整词 / 路径子串出现（`def fib`、调用、导入均可命中）；缺失数量超过
  `max_missing`（默认 0）即拒绝，消息直接列出缺失实体与已覆盖实体。单字符形参
  （`fib(n)` 的 `n`）与 REQ 编号不作为承诺实体，避免误报。
- **反向（提示）**：实现里出现的顶层公共函数 / 类若 spec 从未承诺，默认写入
  evidence 并附在通过消息里提示（防“夹带未承诺功能”），`report_undeclared: false`
  可关闭；反向不拦截。

失败消息示例：

```text
实现未覆盖 spec 承诺实体（缺失 1/2）：fib_fast。请在实现中提供对应函数 / 类 /
接口（或在 spec 中移除未落地的承诺）；已覆盖：fib
```

## 配置

```yaml
semantic:
  requirement_coverage:      # 需求追踪：spec REQ-xxx -> 测试 # REQ-xxx
    enabled: true            # 默认 false
    min_coverage: 100        # 0-100，默认 100
    stages: [2]              # 在哪些当前阶段推进时运行（0-6）
  mutation_score:            # 变异测试：仅 Python，防“空测试 / 假断言”
    enabled: true            # 默认 false
    min_score: 80            # 0-100，默认 80
    max_mutants: 20          # 变异体采样上限（越大越慢）
    timeout_per_mutant: 60   # 单个变异体测试超时（秒）
    seed: 42                 # 采样种子，确定性复现
    # python_bin: ~          # 默认当前解释器
    # command: []            # 自定义测试命令（默认 <python> -m pytest -q -p no:cacheprovider）
    stages: [4]              # 在阶段 4（运行测试）推进时校验
  spec_specificity:          # spec 具体性：拒“套话 spec”（阶段 1）
    enabled: true            # 默认 false
    min_entities: 5          # 具体实体（fib / /api/login / token=）下限
    min_signatures: 2        # 接口签名标记下限
    min_decision_phrases: 1  # 明确技术决策下限
    min_requirement_anchors: 2  # 需求锚点命中下限（0 关闭该子检查）
    max_filler_hits: 1       # 套话句式命中上限
    # filler_patterns: []    # 可覆盖默认套话正则（默认 8 条）
    stages: [1]              # 在阶段 1（Spec 设计）推进时校验
  test_assertion_quality:    # 断言质量：拒 assert True 等（阶段 2，仅 Python）
    enabled: true            # 默认 false
    strict: true             # true=弱函数即拒绝；false=仅警告
    min_assert_targets: 0    # 可选严格档：每测试覆盖 >= N 个不同断言目标（0 关闭）
    require_meaningful_names: false  # 可选命名严格档：名称需含行为动词 / spec 功能关键词
    stages: [2]              # 在阶段 2（测试用例编写）推进时校验
  implementation_traceability:  # 实现-文档双向追踪：spec 承诺实体须落地（阶段 3，仅 Python）
    enabled: true            # 默认 false
    min_entities: 1          # spec 可追踪实体少于该值自动跳过
    max_missing: 0           # 允许缺失实体上限（0=每个承诺实体都须实现）
    report_undeclared: true  # 实现里 spec 未承诺的公共符号提示（不拦截）
    stages: [3]              # 在阶段 3（实现代码）推进时校验
```

`semantic.*` 全部默认关闭；关闭时运行语义校验是无副作用的快速空转。

## 扩展：语义校验器插件

与语言适配器 / 阶段校验器 / 拦截规则同模式，v0.49.0 新增第 5 类入口点组：

```toml
[project.entry-points."phase_barrier.semantic_validators"]
my_semantic = "my_package:MySemanticValidator"
```

校验器契约（基类 `anti_shortcut.SemanticValidator`）：

```python
from anti_shortcut import SemanticCheckResult, SemanticValidator


class MySemanticValidator(SemanticValidator):
    name = "my_semantic"        # 唯一标识；配置开关见下
    description = "一句话说明"
    stages = (2, 4)             # 在哪些当前阶段推进时运行

    def check(self, workspace, config, state, adapter=None) -> SemanticCheckResult:
        # 返回 SemanticCheckResult(ok, message, evidence)；ok=False 即阻止阶段推进
        return SemanticCheckResult(True, "通过", {"note": "..."})
```

启用第三方校验器（按 name 配置，可携带任意选项）：

```yaml
semantic:
  plugin_options:
    my_semantic:
      enabled: true
      # ...自定义选项，check() 通过 config.semantic.plugin_options 读取
```

进程内注册（脚本 / SDK 场景，不打包）：

```python
from anti_shortcut import register_semantic_validator

register_semantic_validator(MySemanticValidator())
```

执行语义校验的入口（SDK / 编排器钩子复用）：

```python
from anti_shortcut import run_semantic_checks

ok, message, evidence = run_semantic_checks(workspace, config, state, stage=2)
# ok=False -> message 含失败明细，evidence 含各校验器明细（留痕 / 人工审查用）
```

校验器自身抛异常不会拖垮门禁：按“失败 + 异常提示”处理并在 evidence 留痕。

## 官方参考插件：LLM 语义审查

`examples/semantic_llm_check/` 提供“需求 ↔ spec ↔ 测试 ↔ 实现”的 LLM 一致性
审查参考实现（第 5 类入口点示例），要点：

- 仅用标准库 `urllib` 调用 OpenAI 兼容 `/chat/completions`，无需额外依赖；
- **离线降级**：未配置 `api_key` / 网络不可用时返回“跳过”而非阻断；
- 配置 `network_required: true` 后，模型判定不一致才阻止阶段推进；
- 结构化输出要求模型只返回 JSON（`consistent` / `reason` / `gaps`），
  解析失败时保守降级不阻断并写入 evidence。

```bash
pip install -e examples/semantic_llm_check     # 注册入口点插件
python examples/semantic_llm_check/demo.py --via-entry-point   # 离线演示（降级跳过）
```

```yaml
semantic:
  plugin_options:
    llm_semantic_check:
      enabled: true
      model: gpt-4o-mini
      # api_key: sk-...                # 或环境变量 LLM_SEMANTIC_CHECK_API_KEY / OPENAI_API_KEY
      # endpoint: https://api.openai.com/v1/chat/completions
      # network_required: true         # true 时不一致即阻断；默认 false 只提示不阻断
      # timeout: 30
```

## 定位与边界

- 语义校验是**增强**而非**替代**结构校验：两者都通过才放行；
- 需求追踪 / 变异测试 / spec 具体性 / 断言质量 / 实现追踪是确定性的、无外部依赖的
  语义增强，成本可控；
- LLM 审查属于可选的第三道防线；“AI 审查 AI”存在理论上的对抗升级空间，
  最终防线仍是人工审查证据包（`export-evidence` / `verify-evidence`）。