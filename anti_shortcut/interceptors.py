"""工具拦截辅助：命令分类、门禁目录保护、shell 写路径提取、测试输出摘要。

- 识别测试运行命令（可配置正则）
- 识别试图访问/篡改门禁目录的命令
- 从 shell 命令中提取“被写入的路径”（重定向、mv/cp 目标、sed -i、rm/touch 等），
  从而让 ``execute_command`` 的写操作受到与 ``write_file`` 相同的阶段限制
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import GateConfig

if TYPE_CHECKING:  # 避免与 languages.base 的互相导入
    from .languages.base import LanguageAdapter


# ---------- 测试命令识别 ----------

def is_test_command(command: str, config: GateConfig) -> bool:
    """判断命令是否为测试运行命令（按 config.test_commands 正则匹配前缀）。"""
    cmd = (command or "").strip()
    if not cmd:
        return False
    for pattern in config.test_commands:
        try:
            if re.search(pattern, cmd, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def is_language_test_command(
    command: str | None,
    config: GateConfig,
    adapter: Any = None,
) -> bool:
    """判断命令是否为测试运行命令（语言适配器版本）。

    优先级：语言适配器的识别规则 > 配置的通用正则 > 关键词兜底。
    关键词兜底匹配独立的 ``test`` 单词（如 ``make test``）或 ``/test`` 脚本路径
    （如 ``./test``、``scripts/test``）。
    防御优先：宁可多拦一步，也不让测试命令在实现完成前漏网；
    真正的识别以适配器规则与 ``config.test_commands`` 为准。
    """
    cmd = (command or "").strip()
    if not cmd:
        return False
    if adapter is not None:
        identify = getattr(adapter, "identify_test_command", None)
        if callable(identify) and identify(cmd):
            return True
    if is_test_command(cmd, config):
        return True
    return bool(re.search(r"(^|[/\s])test([/\s]|$)", cmd, flags=re.IGNORECASE))


# ---------- 门禁目录保护 ----------

def touches_gate_dir(command: str, gate_dir: Path) -> bool:
    """判断命令是否可能访问或篡改门禁目录（.agent_gate）。"""
    gate_dir = Path(gate_dir)
    name = gate_dir.name
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    for raw in tokens:
        tok = raw.strip('"\'')
        if not tok:
            continue
        if tok == name or tok == str(gate_dir):
            return True
        if tok.startswith(name + "/") or tok.startswith(name + "\\"):
            return True
        if tok.startswith(str(gate_dir) + "/") or tok.startswith(str(gate_dir) + "\\"):
            return True
        # 路径段匹配：$HOME/.agent_gate/state.json、/tmp/.agent_gate/x、C:\ws\.agent_gate\y 等
        if re.search(r"(^|[/\\])" + re.escape(name) + r"([/\\]|$)", tok):
            return True
    return False


# ---------- shell 写路径提取 ----------

def _looks_like_sed_expr(token: str) -> bool:
    """粗略判断 token 是否为 sed 表达式（如 s/a/b/、/pattern/、1,5d）。"""
    if "/" in token:
        return True
    return bool(re.match(r"^[0-9$!~,]*[sSdDcCpPaAiI]", token))


def extract_written_paths(command: str) -> list[str]:
    """从 shell 命令中提取可能被写入的路径（启发式，用于阶段门禁检查）。"""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    paths: list[str] = []
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if tok in (">", ">>", "2>", "2>>", "1>", "1>>"):
            if i + 1 < n:
                nxt = tokens[i + 1]
                if not nxt.startswith("&"):
                    paths.append(nxt)
        elif tok in ("mv", "cp", "install", "rename"):
            dest = [t for t in tokens[i + 1:] if not t.startswith("-")]
            if dest:
                paths.append(dest[-1])
        elif tok == "sed":
            j = i + 1
            if j < n and tokens[j].startswith("-i"):
                j += 1  # 跳过 -i 或 -i.bak
                while j < n and tokens[j].startswith("-"):
                    opt = tokens[j]
                    j += 1
                    if opt in ("-e", "--expression", "-f", "--file") and j < n:
                        j += 1
                if j < n and _looks_like_sed_expr(tokens[j]):
                    j += 1  # 跳过 sed 表达式
                for t in tokens[j:]:
                    if not t.startswith("-"):
                        paths.append(t)
        elif tok == "dd":
            for t in tokens[i + 1:]:
                if t.startswith("of="):
                    paths.append(t[3:])
        elif tok in ("rm", "touch", "tee", "truncate", "unlink", "shred"):
            for t in tokens[i + 1:]:
                if t == "--" or t.startswith("-"):
                    continue
                paths.append(t)
        i += 1
    return paths


# ---------- 测试输出摘要 ----------

def summarize_test_output(
    output: str,
    exit_code: int | None = None,
    *,
    max_tail: int = 4000,
) -> dict[str, Any]:
    """把测试命令输出规整为结构化记录：退出码、是否通过、摘要、输出尾部。"""
    text = output or ""
    if exit_code is None:
        passed = bool(re.search(r"\b\d+ passed\b|tests? (passed|ok)\b|All tests? passed", text, re.IGNORECASE))
        if re.search(r"\b\d+ failed\b|FAILED|ERRORS?\b", text, re.IGNORECASE):
            passed = False
    else:
        passed = exit_code == 0

    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    tail = lines[-20:]
    tail_text = "\n".join(tail)[-max_tail:]

    # 尝试提取 pytest / unittest 风格摘要行
    summary = ""
    for ln in reversed(tail):
        if re.search(r"\b\d+ (passed|failed|error|skipped|warning)\b|tests? (passed|ok|failed)\b|OK|FAILED", ln, re.IGNORECASE):
            summary = ln.strip()
            break
    if not summary and tail:
        summary = tail[-1][:300]

    return {
        "exit_code": exit_code,
        "passed": passed,
        "summary": summary,
        "output_tail": tail_text,
    }
