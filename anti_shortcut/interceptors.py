"""工具拦截辅助：命令分类、门禁目录保护、shell 写路径提取、测试输出摘要。

- 识别测试运行命令（可配置正则）
- 识别试图访问/篡改门禁目录的命令
- 从 shell 命令中提取“被写入的路径”（重定向、mv/cp 目标、sed -i、rm/touch 等），
  从而让 ``execute_command`` 的写操作受到与 ``write_file`` 相同的阶段限制
"""
from __future__ import annotations

import importlib.metadata as metadata
import inspect
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .config import GateConfig
from .rules import get_rule

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
        candidates = [tok]
        if "=" in tok:
            value = tok.split("=", 1)[1].strip('"\'')
            if value:
                candidates.append(value)
        for tok in candidates:
            if tok == name or tok == str(gate_dir):
                return True
            if tok.startswith(name + "/") or tok.startswith(name + "\\"):
                return True
            if tok.startswith(str(gate_dir) + "/") or tok.startswith(str(gate_dir) + "\\"):
                return True
            # 路径段匹配：$HOME/.agent_gate/state.json、/tmp/.agent_gate/x、C:\ws\.agent_gate\y 等
            if re.search(r"(^|[^A-Za-z0-9_.\-])" + re.escape(name) + r"([/\\]|$)", tok):
                return True
    return False


# ---------- shell 写路径提取 ----------

def _looks_like_sed_expr(token: str) -> bool:
    """粗略判断 token 是否为 sed 表达式（如 s/a/b/、/pattern/、1,5d）。"""
    if "/" in token:
        return True
    return bool(re.match(r"^[0-9$!~,]*[sSdDcCpPaAiI]", token))


# ---------- 脚本类写入提取（v0.14.0） ----------

_SCRIPT_INTERPRETERS = {
    "python", "python3", "py", "node", "nodejs", "perl", "ruby", "php", "deno", "bun",
    "sh", "bash", "zsh", "ksh", "dash", "pwsh", "powershell",
}
_SCRIPT_EVAL_FLAGS = {"-c", "-e", "-E", "-p", "-r", "--eval"}

_SCRIPT_OPEN_WRITE_RE = re.compile(
    r"""open\(\s*(['"])(?P<path>.+?)\1\s*,\s*(['"])(?P<mode>[rwa+bx]*)\3""",
    re.IGNORECASE,
)
_SCRIPT_PATH_WRITE_RE = re.compile(
    r"""(?:Path|PosixPath|WindowsPath)\(\s*(['"])(?P<path>.+?)\1\s*\)\s*\.\s*write_(?:text|bytes)\(""",
    re.IGNORECASE,
)
_SCRIPT_FS_WRITE_RE = re.compile(
    r"""(?:writeFileSync|writeFile|appendFileSync|appendFile|createWriteStream)\s*\(\s*(['"])(?P<path>.+?)\1""",
    re.IGNORECASE,
)
_SCRIPT_REDIRECT_RE = re.compile(
    r"""(?<![=&\-])>\s*(?:&?\d+)?\s*(?:['"](?P<qpath>[^'"]+)['"]|(?P<ppath>[^\s'"|&;<>]+))"""
)


def _is_script_interpreter(tok: str) -> bool:
    """判断 token 是否为脚本解释器（python / node / perl / ruby / sh 等）。"""
    try:
        name = Path(tok.strip('"\'')).name.lower()
    except ValueError:
        return False
    return name in _SCRIPT_INTERPRETERS


def _looks_like_writable_path(path: str) -> bool:
    """过滤明显不是文件路径的提取结果（空串 / 纯操作符 / 含 shell 元字符）。"""
    if not path:
        return False
    if re.search(r"[;|&<>*\x00\n\r]", path):
        return False
    if re.search(r"[{}]", path):
        return False
    if path.isdigit() or path.startswith("-") or path in (">", ">>"):
        return False
    return True


def _mode_is_write(mode: str) -> bool:
    """open() 的模式是否为写（含 w/a/x/+ ；纯 r/rt/rb 视为读）。"""
    m = (mode or "").lower()
    if not m:
        return False
    return any(ch in m for ch in "wax+")


def _extract_script_write_paths(command: str) -> list[str]:
    """从脚本解释器（python -c / node -e 等）的代码参数中提取可能被写入的路径。

    覆盖：
    - ``open('path', 'w'/'a'/'x'/'r+')`` 与 ``Path('path').write_text / write_bytes``；
    - node ``fs.writeFile(Sync) / appendFile(Sync) / createWriteStream``；
    - 代码内的重定向（``bash -c "cat > out.txt"`` 风格）。
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    paths: list[str] = []
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if not _is_script_interpreter(tok):
            continue
        if i + 1 >= n or tokens[i + 1] not in _SCRIPT_EVAL_FLAGS:
            continue
        if i + 2 >= n:
            continue
        code = tokens[i + 2]
        for m in _SCRIPT_OPEN_WRITE_RE.finditer(code):
            if _mode_is_write(m.group("mode")) and _looks_like_writable_path(m.group("path")):
                paths.append(m.group("path"))
        for m in _SCRIPT_PATH_WRITE_RE.finditer(code):
            if _looks_like_writable_path(m.group("path")):
                paths.append(m.group("path"))
        for m in _SCRIPT_FS_WRITE_RE.finditer(code):
            if _looks_like_writable_path(m.group("path")):
                paths.append(m.group("path"))
        for m in _SCRIPT_REDIRECT_RE.finditer(code):
            p = m.group("qpath") or m.group("ppath")
            if p and _looks_like_writable_path(p):
                paths.append(p)
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


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
        if tok in (">", ">>", "2>", "2>>", "1>", "1>>", "&>", "&>>", ">|"):
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
    for p in _extract_script_write_paths(command):
        if p not in paths:
            paths.append(p)
    return paths


# ---------- 测试输出摘要 ----------

def _extract_coverage(text: str) -> float | None:
    """从测试输出中提取覆盖率百分比（pytest-cov / go test -cover / istanbul 表）。

    支持：
    - ``coverage: 89.1% of statements``（go test -cover）
    - ``TOTAL  5  0  100%``（pytest-cov）
    - ``All files | 100 | 100 | 100 | 100 |``（jest / vitest --coverage，istanbul 表）
    """
    text = re.sub(r"\x1b\[[0-9;]*m", "", text or "")
    m = re.search(r"coverage:\s*([\d.]+)%", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    for ln in text.splitlines():
        if re.match(r"\s*TOTAL\s", ln):
            m = re.search(r"([\d.]+)%", ln)
            if m:
                return float(m.group(1))
    for ln in text.splitlines():
        if "All files" in ln:
            # v0.16.0：支持千分位分隔符（如 1,234.56 ）
            m = re.search(r"All files[^|]*\|\s*([\d,.]+)", ln)
            if m:
                return float(m.group(1).replace(",", ""))
    return None


def summarize_test_output(
    output: str,
    exit_code: int | None = None,
    *,
    max_tail: int = 4000,
    adapter: Any = None,
) -> dict[str, Any]:
    """把测试命令输出规整为结构化记录：退出码、是否通过、摘要、输出尾部。

    v0.10.0：传入 ``adapter`` 时优先使用语言适配器的 ``parse_test_output``
    生成语言专属摘要（如 Go 的 ``ok pkg 0.5s``、Rust 的 ``test result: ok. 2 passed``），
    失败时包含具体失败用例名；适配器异常不影响主流程。
    """
    text = re.sub(r"\x1b\[[0-9;]*m", "", output or "")
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
    # 两遍扫描：优先 pytest/统计行（passed / Tests run / Failures），
    # 再退而求其次找 BUILD SUCCESS/FAILURE、OK、FAILED（Maven/Gradle 风格）
    summary = ""
    for ln in reversed(tail):
        if re.search(r"\b\d+ (passed|failed|error|skipped|warning)\b|tests? (passed|ok|failed)\b|Tests? run:\s*\d+|Failures?:\s*\d+|Errors?:\s*\d+", ln, re.IGNORECASE):
            summary = ln.strip()
            break
    if not summary:
        for ln in reversed(tail):
            if re.search(r"BUILD (SUCCESS|FAILURE)|OK|FAILED", ln, re.IGNORECASE):
                summary = ln.strip()
                break
    if not summary and tail:
        summary = tail[-1][:300]

    # v0.10.0：语言适配器摘要优先（Go / Rust / Java / JS 专属输出）
    if adapter is not None:
        try:
            a_passed, a_summary = adapter.parse_test_output(text, exit_code)
        except Exception:  # pragma: no cover（适配器解析异常不影响门禁）
            a_passed, a_summary = None, ""
        if a_passed is not None and exit_code is None:
            passed = a_passed
        if a_summary:
            summary = a_summary

    return {
        "exit_code": exit_code,
        "passed": passed,
        "summary": summary,
        "output_tail": tail_text,
        "coverage": _extract_coverage(text),
    }
# ---------- 自定义拦截规则（v0.12.0） ----------

INTERCEPTOR_ENTRY_POINT_GROUP = "phase_barrier.interceptors"

# 进程内规则注册表：[(name, rule)]，规则签名：
#   rule(kind: str, target: str, config: GateConfig, stage: int) -> tuple[bool, str] | None
#   - kind: "write"（路径）或 "exec"（shell 命令）
#   - 返回 (False, reason) 拦截；(True, reason) 放行（短路）；None 弃权
_rule_registry: list[tuple[str, Callable]] = []


def register_rule(name: str, rule: Callable) -> None:
    """进程内注册一条自定义拦截规则。

    :param name: 规则名（重复注册会覆盖同名旧规则）
    :param rule: ``rule(kind, target, config, stage) -> (bool, str) | None``
    """
    if not callable(rule):
        raise TypeError("rule 必须可调用（kind, target, config, stage）")
    for i, (existing, _) in enumerate(_rule_registry):
        if existing == name:
            _rule_registry[i] = (name, rule)
            return
    _rule_registry.append((name, rule))


def _coerce_rules(obj: Any) -> list[Callable]:
    """把入口点对象规整为规则函数列表。

    支持四种形式：
    - 带 ``rules`` 属性（函数列表 / 元组）
    - ``{name: rule}`` 字典
    - 可调用工厂，返回规则函数列表
    - 本身就是规则函数（单条规则）
    """
    rules = getattr(obj, "rules", None)
    if rules is not None:
        return [r for r in rules if callable(r)]
    if isinstance(obj, dict):
        return [v for v in obj.values() if callable(v)]
    if callable(obj):
        try:
            result = obj()
        except TypeError:
            return [obj]
        if isinstance(result, (list, tuple)):
            return [r for r in result if callable(r)]
    return []


def load_rule_plugins() -> list[tuple[str, Callable]]:
    """加载 ``phase_barrier.interceptors`` 入口点注册的拦截规则。

    入口点对象可以是规则函数、返回规则列表的工厂、``{name: rule}`` 映射或
    带 ``rules`` 属性的对象；单个入口点加载失败时跳过，不影响其他入口点。
    进程内 ``register_rule`` 注册的规则优先级高于入口点。
    """
    rules: list[tuple[str, Callable]] = list(_rule_registry)
    try:
        eps = metadata.entry_points(group=INTERCEPTOR_ENTRY_POINT_GROUP)
    except TypeError:  # Python 3.9- 旧接口（requires-python>=3.10，仅防御）
        eps = metadata.entry_points().get(INTERCEPTOR_ENTRY_POINT_GROUP, [])
    for ep in eps:
        try:
            for rule in _coerce_rules(ep.load()):
                rules.append((f"ep:{ep.name}:{len(rules)}", rule))
        except Exception:
            continue
    return rules


def _call_rule(
    rule: Callable,
    kind: str,
    target: str,
    config: GateConfig | None,
    stage: int,
    content: str | None,
) -> Any:
    """调用规则：签名接受 ``content`` 时传入，否则按旧签名调用（向后兼容）。"""
    try:
        sig = inspect.signature(rule)
    except (TypeError, ValueError):
        sig = None
    if sig is not None and "content" in sig.parameters:
        try:
            return rule(kind, target, config, stage, content=content)
        except TypeError:
            return None
    return rule(kind, target, config, stage)


def _builtin_rules_for(config: GateConfig | None):
    """按 ``config.rules`` 启用内置安全规则包（v0.26.0）。"""
    for name in getattr(config, "rules", None) or []:
        rule = get_rule(name)
        if rule is not None:
            yield name, rule


def evaluate_rules(
    kind: str,
    target: str,
    config: GateConfig | None,
    stage: int,
    content: str | None = None,
) -> tuple[bool | None, str]:
    """按注册顺序评估拦截规则，返回首个决定性结论。

    v0.26.0：先评估 ``config.rules`` 启用的内置安全规则包，再评估
    ``phase_barrier.interceptors`` 入口点 / 进程内注册的自定义规则；
    write 类规则可接收待写内容（规则签名带 ``content`` 参数时才会收到）。

    :param kind: ``"write"`` 或 ``"exec"``
    :param target: 路径字符串或 shell 命令
    :param content: 待写入内容（write 场景可选）
    :return: (None, "") 全部弃权；(False, reason) 拦截；(True, reason) 放行
    """
    if kind not in ("write", "exec"):
        raise ValueError(f"kind 必须是 'write' 或 'exec': {kind!r}")
    for _, rule in _builtin_rules_for(config):
        try:
            result = _call_rule(rule, kind, target, config, stage, content)
        except Exception:
            continue  # 规则异常不阻断门禁
        if result is None:
            continue
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
            return result
    for _, rule in load_rule_plugins():
        try:
            result = _call_rule(rule, kind, target, config, stage, content)
        except Exception:
            continue  # 规则异常不阻断门禁
        if result is None:
            continue
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
            return result
    return None, ""
