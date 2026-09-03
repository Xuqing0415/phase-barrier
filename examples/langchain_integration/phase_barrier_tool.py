"""LangChain PhaseBarrierTool（BaseTool 子类，v0.32.0）。

把 phase-barrier 的 ``GateClient`` 包装成 ``langchain_core.tools.BaseTool`` 子类：

- ``PhaseBarrierWriteTool``：经门禁写文件（参数 path / content）
- ``PhaseBarrierExecTool``：经门禁执行 shell 命令（参数 command）

被拦截时返回 JSON ``{"ok": false, "denied": "..."}``（不抛异常），LLM 读取原因后
可按 SOP 补写 spec / 测试再重试。langchain 为可选依赖：未安装 langchain-core 时
模块可正常导入（``HAS_LANGCHAIN=False``），实例化会抛出带安装提示的 RuntimeError。

用法（安装 langchain-core 后）::

    from anti_shortcut.proxy_client import GateClient
    from phase_barrier_tool import PhaseBarrierWriteTool

    gate = GateClient("http://sidecar:8080")
    tool = PhaseBarrierWriteTool(gate=gate)
    tool.invoke({"path": "fib.py", "content": "..."})   # 跳步 -> denied JSON

运行：python examples/langchain_integration/demo.py
"""
from __future__ import annotations

import json
from typing import Any, Type

from pydantic import BaseModel, Field

from anti_shortcut.proxy_client import GateClient, GateDenied

try:  # langchain 为可选依赖：仅安装 langchain-core 后 BaseTool 才可用
    from langchain_core.tools import BaseTool

    HAS_LANGCHAIN = True
except Exception:  # pragma: no cover - 环境差异（未安装 langchain-core）
    BaseTool = object
    HAS_LANGCHAIN = False


class WriteArgs(BaseModel):
    """gate_write_file 工具入参。"""

    path: str = Field(description="相对工作区的文件路径，如 spec.md / src/fib.py")
    content: str = Field(description="要写入的文件内容")


class ExecArgs(BaseModel):
    """gate_execute_command 工具入参。"""

    command: str = Field(description="要执行的 shell 命令，如 python -m pytest -q")


class PhaseBarrierTool(BaseTool):
    """携带 ``GateClient`` 的门禁 BaseTool 基类（请使用子类）。"""

    gate: Any = Field(default=None, exclude=True, description="GateClient 实例")

    def __init__(self, gate: GateClient, **kwargs: Any) -> None:
        if not HAS_LANGCHAIN:
            raise RuntimeError(
                "PhaseBarrierTool 需要 langchain-core（pip install 'langchain-core>=0.3'）"
            )
        super().__init__(gate=gate, **kwargs)


class PhaseBarrierWriteTool(PhaseBarrierTool):
    """经门禁写文件：未完成前置阶段时返回 denied JSON。"""

    name: str = "gate_write_file"
    description: str = (
        "经 phase-barrier 阶段门禁写入工作区文件（path 为相对路径，content 为内容）；"
        "跳步（如未写 spec 就写实现代码、未写测试就写实现）会被拒绝并返回 denied 原因"
    )
    args_schema: Type[BaseModel] = WriteArgs

    def _run(self, path: str, content: str, **_: Any) -> str:
        try:
            self.gate.write_file(path, content)
        except GateDenied as exc:
            return json.dumps({"ok": False, "denied": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "path": path}, ensure_ascii=False)


class PhaseBarrierExecTool(PhaseBarrierTool):
    """经门禁执行 shell 命令：测试命令执行结果会被记录。"""

    name: str = "gate_execute_command"
    description: str = (
        "经 phase-barrier 阶段门禁执行 shell 命令（command）；测试命令执行结果会被记录，"
        "未完成实现就运行测试会被拒绝"
    )
    args_schema: Type[BaseModel] = ExecArgs

    def _run(self, command: str, **_: Any) -> str:
        try:
            result = self.gate.execute_command(command)
        except GateDenied as exc:
            return json.dumps({"ok": False, "denied": str(exc)}, ensure_ascii=False)
        return json.dumps({**result, "ok": True}, ensure_ascii=False)
