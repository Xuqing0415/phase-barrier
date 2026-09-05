"""示例阶段校验器：阶段 1 额外要求 alpha-review.md（fixture，仅测试用）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def require_alpha_spec(
    workspace: str | Path,
    config: Any,
    state: dict[str, Any] | None = None,
    adapter: Any = None,
) -> tuple[bool, str, dict]:
    spec = Path(workspace) / getattr(config, "spec_file", "spec.md")
    if not spec.exists():
        return False, "缺少 spec 文件：请先完成 Spec 设计", {}
    review = Path(workspace) / "alpha-review.md"
    if not review.exists():
        return False, "自定义门禁：缺少 alpha-review.md（阶段 1 额外证据）", {}
    return True, "spec 与 alpha-review 均已提供", {"alpha_review": True}


require_alpha_spec.stage = 1