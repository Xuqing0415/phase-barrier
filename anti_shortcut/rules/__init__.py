"""内置安全拦截规则包（v0.26.0）：随配置一键启用的常见安全规则。

每条规则的签名与自定义拦截规则一致：:

    rule(kind, target, config, stage, content=None) -> (bool, str) | None

- ``kind``：``"write"``（target 为路径，content 为待写内容，可能为 None）或 ``"exec"``（target 为 shell 命令）
- 返回 ``(False, reason)`` 拦截 / ``(True, reason)`` 放行 / ``None`` 弃权

启用方式（YAML）：::

    rules:
      - no_path_traversal
      - no_hardcoded_secrets
    rules_options:
      license_header: "Copyright (c) 2026 Example Corp."

内置规则（规则名 -> 描述）：

- ``no_shell_injection``：拦截 exec 命令中的 shell 命令链接 / 命令替换（``;`` / ``&&`` / ``||`` / ``$(...)`` / 反引号）
- ``no_path_traversal``：拦截 write 中解析后越出工作区的路径（``..`` 逃逸 / 绝对路径逃逸）
- ``no_hardcoded_secrets``：拦截写入内容中疑似密码 / API 密钥 / 私钥的硬编码
- ``require_license_header``：拦截未携带指定许可证头的源文件写入（头文本取 ``rules_options.license_header``）
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Callable

__all__ = ["BUILTIN_RULES", "RULE_DESCRIPTIONS", "get_rule"]

# 规则名 -> 规则函数；规则签名 (kind, target, config, stage, content=None)
BUILTIN_RULES: dict[str, Callable] = {}

RULE_DESCRIPTIONS: dict[str, str] = {
    "no_shell_injection": "拦截 exec 命令中的 shell 命令链接 / 命令替换（; / && / || / $(...) / 反引号）",
    "no_path_traversal": "拦截 write 中解析后越出工作区的路径（.. 逃逸 / 绝对路径逃逸）",
    "no_hardcoded_secrets": "拦截写入内容中疑似密码 / API 密钥 / 私钥的硬编码",
    "require_license_header": "拦截未携带指定许可证头的源文件写入（rules_options.license_header）",
}

# 疑似密钥 / 密码的常见赋值模式
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"auth[_-]?token|client[_-]?secret|token)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{8,}"
)
_AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")

# 源文件扩展名（require_license_header 适用范围）
_SOURCE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rs", ".rb",
    ".cs", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".sh", ".kt", ".swift",
}


def _shell_has_injection(command: str) -> bool:
    """检测 shell 命令中的链接 / 命令替换运算符（跳过引号内内容）。

    用 ``shlex(..., punctuation_chars=True)`` 分词：``;`` / ``&&`` / ``||`` /
    ``$(` / ``)`` 等在引号外会作为独立 token 出现；引号内的内容保持原样，
    因此 ``python -c 'import sys; print(1)'`` 不会被误判。
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok in (";", "&&", "||"):
            return True
        if tok.startswith("$(") or "`" in tok:
            return True
        # ``$(`` 被 shlex 切成 ``$`` 与 ``(``（赋值形式 ``x=$(...)`` 为 ``x=$`` + ``(``）
        if (tok == "$" or tok.endswith("$")) and i + 1 < n and tokens[i + 1].startswith("("):
            return True
        # 结尾反斜杠续行（`cmd \` 后接另一条命令）
        if tok.endswith("\\") and tok not in ("\\",):
            return True
    return False


def no_shell_injection(kind, target, config, stage, content=None):
    """拦截 exec 命令中的 shell 链接 / 命令替换（严格模式，按需启用）。"""
    if kind != "exec":
        return None
    cmd = str(target or "")
    if _shell_has_injection(cmd):
        return False, "no_shell_injection：命令包含 shell 命令链接 / 命令替换运算符（; && || $(...) `），请拆分为单条命令"
    return None


def no_path_traversal(kind, target, config, stage, content=None):
    """拦截 write 中解析后越出工作区的路径。"""
    if kind != "write":
        return None
    raw = str(target or "")
    p = Path(raw)
    ws = Path(getattr(config, "workspace", Path("."))).resolve() if config is not None else None
    try:
        resolved = (ws / p).resolve() if ws is not None and not p.is_absolute() else p.resolve()
    except OSError:
        return False, f"no_path_traversal：无法解析路径 {raw!r}"
    if ws is not None:
        try:
            resolved.relative_to(ws)
            return None
        except ValueError:
            pass
        # 允许解析后恰为工作区本身（写入根目录场景）
        if resolved == ws:
            return None
        return False, f"no_path_traversal：路径 {raw!r} 解析后位于工作区之外（{resolved}）"
    return None


def no_hardcoded_secrets(kind, target, config, stage, content=None):
    """拦截写入内容中疑似硬编码密码 / API 密钥 / 私钥。"""
    if kind != "write" or not content:
        return None
    text = str(content)
    if _PRIVATE_KEY_RE.search(text):
        return False, "no_hardcoded_secrets：内容包含私钥块（-----BEGIN ... PRIVATE KEY-----）"
    if _AWS_ACCESS_KEY_RE.search(text):
        return False, "no_hardcoded_secrets：内容包含疑似 AWS Access Key（AKIA...）"
    m = _SECRET_ASSIGN_RE.search(text)
    if m:
        return False, f"no_hardcoded_secrets：内容包含疑似硬编码密钥（{m.group(0)[:40]}...）"
    return None


def require_license_header(kind, target, config, stage, content=None):
    """拦截未携带指定许可证头的源文件写入（头文本取 ``rules_options.license_header``）。"""
    if kind != "write":
        return None
    header = None
    if config is not None:
        options = getattr(config, "rules_options", None) or {}
        header = (options or {}).get("license_header")
    if not header:
        return None  # 未配置头文本：弃权（不静默拦截）
    if Path(str(target)).suffix.lower() not in _SOURCE_EXTS:
        return None
    if not content:
        return None
    text = str(content)
    # 允许 shebang 行出现在许可证头之前
    stripped = text
    if stripped.startswith("#!"):
        first_nl = stripped.find("\n")
        stripped = stripped[first_nl + 1 :] if first_nl >= 0 else ""
    if str(header).strip() not in stripped:
        return False, "require_license_header：源文件缺少许可证头，请在 rules_options.license_header 中配置头文本后重试"
    return None


BUILTIN_RULES = {
    "no_shell_injection": no_shell_injection,
    "no_path_traversal": no_path_traversal,
    "no_hardcoded_secrets": no_hardcoded_secrets,
    "require_license_header": require_license_header,
}


def get_rule(name: str) -> Callable | None:
    """按名称取内置规则（不存在返回 None）。"""
    return BUILTIN_RULES.get(name)
