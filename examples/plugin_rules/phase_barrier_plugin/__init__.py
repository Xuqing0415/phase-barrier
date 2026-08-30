"""示例插件：自定义阶段校验器 + 自定义拦截规则。

打包为独立包后，通过 pyproject.toml 声明入口点即可被 phase-barrier 自动加载：
- ``phase_barrier.validators``：覆盖阶段 1 校验（额外要求 design-review.md）；
- ``phase_barrier.interceptors``：追加拦截规则（禁止写入 vendor/）。
"""


def require_design_review(workspace, config, state, adapter=None):
    """覆盖阶段 1：除 spec.md 外还必须提供 design-review.md。"""
    spec = workspace / config.spec_file
    if not spec.exists():
        return False, "缺少 spec 文件：请先完成 Spec 设计", {}
    review = workspace / "design-review.md"
    if not review.exists():
        return False, "自定义门禁：缺少 design-review.md（阶段 1 额外证据）", {}
    return True, "spec 与 design-review 均已提供", {"design_review": True}


require_design_review.stage = 1  # 入口点加载时据此注册到阶段 1


def deny_vendor_writes(kind, target, config, stage):
    """拦截规则：禁止向 vendor/ 写入任何文件（请在规则签名内保持弃权语义）。"""
    norm = str(target).replace("\\", "/")
    if kind == "write" and (norm.startswith("vendor/") or "/vendor/" in norm):
        return False, "自定义规则：禁止写入 vendor/（请用包管理器锁定依赖版本）"
    return None
