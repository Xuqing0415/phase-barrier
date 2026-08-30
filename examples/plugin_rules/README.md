# phase-barrier 插件示例（自定义校验器 + 拦截规则）

本目录演示 phase-barrier 插件机制的两种接入方式：

1. **进程内注册**：`register_validator(stage, fn)` / `register_rule(name, rule)`，无需打包安装；
2. **入口点加载**：打包为独立包，声明 `phase_barrier.validators` / `phase_barrier.interceptors`
   入口点，`pip install` 后由 phase-barrier 自动发现并生效。

## 运行

```bash
# 方式一：进程内注册（零安装）
python demo.py

# 方式二：入口点（先安装本示例包）
pip install -e .
python demo.py --via-entry-point
```

预期输出（方式一）：

```text
进程内已注册：register_validator / register_rule
advance(2) 无 design-review -> success=False: 自定义门禁：缺少 design-review.md（阶段 1 额外证据）
advance(2) 有 design-review -> success=True: 已进入阶段 2（测试用例编写）...
vendor/ 写入被拦截 -> 自定义规则：禁止写入 vendor/（请用包管理器锁定依赖版本）
OK: 插件示例运行完成
```

## 插件内容

- `phase_barrier_plugin/__init__.py`
  - `require_design_review`：覆盖**阶段 1** 校验器，除 `spec.md` 外还要求 `design-review.md`
    （通过 `require_design_review.stage = 1` 声明所属阶段）；
  - `deny_vendor_writes`：拦截规则，禁止向 `vendor/` 写入任何文件
    （规则签名 `rule(kind, target, config, stage)`，返回 `(False, reason)` 拦截 / `(True, reason)` 放行 / `None` 弃权）。

## 打包发布自己的插件

参照本目录 `pyproject.toml`，在插件包的 `[project.entry-points]` 声明两个入口点组即可：

```toml
[project.entry-points."phase_barrier.validators"]
my_stage_rule = "my_pkg.module:my_validator"   # 函数需带 .stage = N 属性（或 {stage: fn} 映射 / 工厂）

[project.entry-points."phase_barrier.interceptors"]
my_rule = "my_pkg.module:my_rule"              # rule(kind, target, config, stage) -> (bool, str) | None
```

安装该包后，phase-barrier 在 `advance_stage`（校验器）与 `check_write_permission` /
`check_exec_permission`（拦截规则）时会自动加载，无需修改宿主代码。
