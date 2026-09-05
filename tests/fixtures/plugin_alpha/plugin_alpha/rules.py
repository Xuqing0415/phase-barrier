"""示例拦截规则：禁止写入 alpha_tmp/（fixture，仅测试用）。"""
from __future__ import annotations

from typing import Any


def deny_alpha_tmp_writes(kind: str, target: Any, config: Any, stage: int, content: str | None = None):
    norm = str(target).replace("\\", "/")
    if kind == "write" and (norm.startswith("alpha_tmp/") or "/alpha_tmp/" in norm):
        return False, "自定义规则：禁止写入 alpha_tmp/"
    return None