"""SWE-bench 容器内 Agent 运行器（baseline / gated 双模式，2026-09）。

在官方评测镜像里运行：镜像的 ``/testbed`` 就是该实例 ``base_commit`` 的检出，
依赖已装在镜像的 testbed conda 环境里。本脚本用镜像的 base conda python 执行门禁
（``anti_shortcut`` 来自挂载进容器的本仓库），测试命令则通过 ``--venv`` 指向
testbed 解释器，两者互不影响。

契约（与 ``scripts/run_swebench_batch_container.py`` 对齐）::

    <gate-python> run_agent.py --dataset DS.json --instance IID --workdir /testbed \
        --venv /opt/miniconda3/envs/testbed/bin/python --mode gated --label L \
        --outdir /pb/.pytest_tmp/bench_data/agent_runs --max-turns 60 --exec-timeout 300

产出：

- ``{outdir}/{label}/model_patch.diff``：``git add -A && git diff HEAD`` 的结果
  （``.agent_gate`` 与 spec.md 经 ``.git/info/exclude`` 排除，不进补丁）
- ``{outdir}/{label}/stats.json``：轮数、耗时、门禁指标
- stdout 标记：``PB_TURNS`` / ``PB_DIFF_CHARS`` / ``PB_GATE_INTERCEPTS`` /
  ``PB_GATE_FINAL_STAGE`` / ``PB_GATE_COMPLETED``
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
API_URL = os.environ.get("PB_DS_API_URL", "https://api.deepseek.com/chat/completions")
DEFAULT_MODEL = os.environ.get("PB_DS_MODEL") or "deepseek-v4-flash"
MAX_TOOL_OUTPUT = 6000
GATE_EXCLUDES = (".agent_gate", "spec.md")

SYSTEM_COMMON = """你是运行在 SWE-bench 容器里的编码 Agent。工作目录 {workdir} 是目标仓库在 base commit 的检出。

可用工具：read_file / write_file / execute_command。
- write_file(path, content)：写文件，path 相对工作目录。
- execute_command(command)：在 {workdir} 下执行 bash 命令；命令里的 python/pytest 已指向该实例 testbed 环境。
- read_file(path, start, limit)：读文件片段。定位问题请用 execute_command 配合 grep/sed。

【问题陈述】
{problem}

【要求】
- 先侦查（读相关源码 / 运行相关测试）再动手，不要凭猜测改代码。
- 修改必须落在该仓库的源码上，让问题陈述里的失败用例通过，且不破坏既有测试。
- 完成修复后用 execute_command 运行相关测试确认通过，然后停止。
"""

SYSTEM_GATED = SYSTEM_COMMON + """
【阶段门禁（必须严格遵守）】
本模式启用了 phase-barrier 阶段门禁。你只能逐级推进，每次推进调用 advance_stage(next_stage)
（next_stage 必须是当前阶段 + 1）。门禁会校验证据，证据不足会被拒绝并给出原因，请按原因补齐后再推进。

- 阶段 1（Spec 设计）：write_file("spec.md", 内容)，内容必须包含 "## 需求分析"、"## 设计方案"、
  "## 接口定义" 三个小节且足够具体（>=120 字符）；然后 advance_stage(2)。
- 阶段 2（测试用例）：写出能复现该问题的测试文件（如 test_pb_repro.py），至少 1 个 test 函数且带 assert；
  然后 advance_stage(3)。
- 阶段 3（实现）：修改源码完成修复；然后 advance_stage(4)。
- 阶段 4（运行测试）：用 execute_command 运行你写的测试；全部通过后 advance_stage(5)（门禁会自动确认交付）。
- 若测试未通过：修复后重新运行测试，再 advance_stage(5)。

不要试图跳过阶段（例如没写 spec 就写实现）；跳跃会被拒绝。
"""

SYSTEM_BASELINE = SYSTEM_COMMON + """
本模式无阶段门禁：直接按你判断的最高效方式修复，完成后停止。
"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入（覆盖）一个文件，路径相对工作目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作目录的文件路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "在工作目录下执行一条 bash 命令，返回 exit_code 与输出。",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的 bash 命令"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件的一段内容（按行）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start": {"type": "integer", "description": "起始行（1 起，默认 1）"},
                    "limit": {"type": "integer", "description": "最多读取行数（默认 200）"},
                },
                "required": ["path"],
            },
        },
    },
]

ADVANCE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "advance_stage",
        "description": "推进到下一个阶段（只能是当前阶段 + 1）。门禁会校验当前阶段证据，失败时返回原因。",
        "parameters": {
            "type": "object",
            "properties": {
                "new_stage": {"type": "integer", "description": "要进入的阶段号（当前阶段 + 1）"}
            },
            "required": ["new_stage"],
        },
    },
}


def load_instance(dataset: Path, instance_id: str) -> dict:
    """从数据集 JSON（数组或 {instances|data|rows: [...]} 包装）里取一个实例。"""
    raw = json.loads(Path(dataset).read_text(encoding="utf-8"))
    rows = raw
    if isinstance(raw, dict):
        for key in ("instances", "data", "rows", "tasks"):
            if isinstance(raw.get(key), list):
                rows = raw[key]
                break
    for row in rows:
        if str(row.get("instance_id")) == instance_id:
            return row
    raise SystemExit(f"数据集 {dataset} 中没有实例 {instance_id}")


def _venv_env(venv: str | None) -> dict:
    env = dict(os.environ)
    if venv:
        env["PATH"] = str(Path(venv).parent) + os.pathsep + env.get("PATH", "")
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
    return env


class AgentTools:
    """Agent 可调用的工具集；gated 模式下会被 AntiShortcutSkill 包装。"""

    def __init__(self, workdir: Path, venv: str | None, exec_timeout: int) -> None:
        self.workdir = workdir
        self.venv = venv
        self.exec_timeout = exec_timeout
        self.env = _venv_env(venv)
        # gated 模式下由 AntiShortcutSkill.install 填入门禁包装后的工具
        self.bound: dict = {}

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (self.workdir / p)

    def write_file(self, path: str, content: str) -> dict:
        target = self._resolve(str(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8", errors="replace")
        return {"ok": True, "path": str(target.relative_to(self.workdir)) if
                str(target).startswith(str(self.workdir)) else str(target),
                "chars": len(str(content))}

    def read_file(self, path: str, start: int = 1, limit: int = 200) -> dict:
        target = self._resolve(str(path))
        if not target.exists():
            return {"ok": False, "error": f"文件不存在: {path}"}
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start))
        chunk = lines[start - 1: start - 1 + max(1, int(limit))]
        numbered = "\n".join(f"{start + i:>6}\t{line}" for i, line in enumerate(chunk))
        return {"ok": True, "path": str(path), "total_lines": len(lines), "content": numbered}

    def execute_command(self, command: str) -> dict:
        try:
            proc = subprocess.run(
                str(command), shell=True, cwd=str(self.workdir), env=self.env,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.exec_timeout,
            )
        except subprocess.TimeoutExpired:
            return {"exit_code": 124, "output": f"命令超时（>{self.exec_timeout}s）"}
        output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        return {"exit_code": proc.returncode, "output": output[-MAX_TOOL_OUTPUT:]}

    def dispatch(self, name: str, args: dict) -> dict:
        bound = self.bound.get(name)
        if name == "write_file":
            func = bound or self.write_file
            return func(args.get("path", ""), args.get("content", ""))
        if name == "read_file":
            return self.read_file(args.get("path", ""), args.get("start", 1), args.get("limit", 200))
        if name == "execute_command":
            func = bound or self.execute_command
            return func(args.get("command", ""))
        return {"ok": False, "error": f"未知工具: {name}"}


def call_model(messages: list, tools: list, model: str, api_key: str, timeout: int = 180) -> dict:
    """调用 OpenAI 兼容的 chat completions 接口，带 3 次重试。"""
    payload = {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": 4096}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    last_error = ""
    for attempt in range(3):
        try:
            request = urllib.request.Request(
                API_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # 4xx/5xx 都重试，便于躲过瞬时限流
            body = exc.read().decode("utf-8", "replace")[:400]
            last_error = f"HTTP {exc.code}: {body}"
        except Exception as exc:  # noqa: BLE001 - 网络异常统一重试
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"模型调用失败（3 次重试后）：{last_error}")


def gate_completed(skill) -> int:
    """交付收尾校验：仅当阶段 6 且「最终测试全绿且覆盖最新代码」时计 1。

    只看 ``is_complete``（阶段 >= 6）会把「推进到阶段 6 后又改码 / 跑红测」的运行误计为已交付；
    这里改用 ``State.delivery_clean()``（阶段 5 回归校验同构、可随时重算）。
    """
    if skill is None:
        return 0
    try:
        return int(bool(skill.state.delivery_clean()))
    except Exception:  # noqa: BLE001 - 没有状态时不算完成
        return 0


def _clamp(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"\n...[截断，共 {len(text)} 字符]"


def _strip_message(message: dict) -> dict:
    keep = {k: v for k, v in message.items() if k in ("role", "content", "tool_calls")}
    keep.setdefault("role", "assistant")
    if keep.get("content") is None and not keep.get("tool_calls"):
        keep["content"] = ""
    return keep


def extract_patch(workdir: Path) -> tuple[str, str]:
    """``git add -A && git diff HEAD`` 得到补丁；返回 (patch, 错误信息)。"""
    exclude = workdir / ".git" / "info" / "exclude"
    if exclude.parent.exists():
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        missing = [item for item in GATE_EXCLUDES if item not in current.splitlines()]
        if missing:
            with open(exclude, "a", encoding="utf-8") as fh:
                fh.write("\n" + "\n".join(missing) + "\n")
    subprocess.run(["git", "add", "-A"], cwd=str(workdir), capture_output=True, text=True)
    proc = subprocess.run(["git", "diff", "HEAD"], cwd=str(workdir),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        return "", (proc.stderr or "").strip()
    return proc.stdout or "", ""


def run(args) -> int:
    started = time.time()
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir = Path(args.outdir) / args.label
    outdir.mkdir(parents=True, exist_ok=True)

    instance = load_instance(args.dataset, args.instance)
    problem = str(instance.get("problem_statement") or instance.get("problem") or "").strip()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("缺少 DEEPSEEK_API_KEY", file=sys.stderr)
        return 2

    subprocess.run(["git", "config", "--global", "user.email", "pb@example.com"],
                   capture_output=True, text=True)
    subprocess.run(["git", "config", "--global", "user.name", "phase-barrier"],
                   capture_output=True, text=True)
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(workdir)],
                   capture_output=True, text=True)

    gate = None
    intercepts = 0
    tools = AgentTools(workdir, args.venv, args.exec_timeout)
    schemas = list(TOOL_SCHEMAS)
    if args.mode == "gated":
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from anti_shortcut import AntiShortcutSkill  # 延迟导入：baseline 模式无需门禁依赖

        gate = AntiShortcutSkill(
            workdir,
            config={"require_assert_per_test": False, "min_test_functions": 1},
            user_request=problem or args.instance,
        )
        tools.bound = gate.install(
            {"write_file": tools.write_file, "execute_command": tools.execute_command}
        )
        schemas.append(ADVANCE_SCHEMA)

    # 不能用 str.format：问题陈述里常含 {{ }}（代码片段），会触发 KeyError
    template = SYSTEM_GATED if gate else SYSTEM_BASELINE
    system = (template.replace("{workdir}", str(workdir)).replace("{problem}", problem))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": "开始吧，按阶段完成任务。"}]

    turns = 0
    idle = 0
    model_used = args.model
    for turn in range(1, args.max_turns + 1):
        turns = turn
        try:
            response = call_model(messages, schemas, args.model, api_key)
        except RuntimeError as exc:
            print(f"[turn {turn}] 模型错误: {exc}", file=sys.stderr)
            break
        model_used = response.get("model") or model_used
        message = (response.get("choices") or [{}])[0].get("message") or {}
        messages.append(_strip_message(message))
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            idle += 1
            print(f"[turn {turn}] assistant: {_clamp(str(message.get('content') or ''), 300)}")
            if gate is not None and gate.is_complete:
                break
            if idle >= 3:
                break
            messages.append({"role": "user", "content": "请继续调用工具完成任务；若已完成请说明。"})
            continue
        idle = 0
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name") or ""
            try:
                call_args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                call_args = {}
            if not isinstance(call_args, dict):
                call_args = {}
            if name == "advance_stage":
                try:
                    result = gate.advance_stage(int(call_args.get("new_stage", 0)))
                except Exception as exc:  # noqa: BLE001
                    result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
                if not result.get("success"):
                    intercepts += 1
            else:
                try:
                    result = tools.dispatch(name, call_args)
                except PermissionError as exc:
                    intercepts += 1
                    result = {"gate_denied": str(exc)}
                except Exception as exc:  # noqa: BLE001
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                if isinstance(result, dict) and result.get("gate_denied"):
                    intercepts += 1
            print(f"[turn {turn}] {name} -> "
                  f"{_clamp(json.dumps(result, ensure_ascii=False), 160)}")
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": _clamp(json.dumps(result, ensure_ascii=False))})
        if gate is not None and gate.is_complete:
            print(f"[turn {turn}] 门禁：已进入交付阶段，结束。")
            break

    patch, err = extract_patch(workdir)
    if err:
        print(f"补丁提取警告: {err}", file=sys.stderr)
    (outdir / "model_patch.diff").write_text(patch, encoding="utf-8", newline="\n")
    seconds = round(time.time() - started, 1)

    final_stage = gate.current_stage if gate else ""
    completed = gate_completed(gate)  # 交付收尾校验：最终测试全绿才算完成
    stats = {
        "instance_id": args.instance,
        "mode": args.mode,
        "label": args.label,
        "model": model_used,
        "turns": turns,
        "seconds": seconds,
        "diff_chars": len(patch),
        "gate_intercepts": intercepts if gate else 0,
        "gate_final_stage": final_stage,
        "gate_completed": completed,
        "delivery_clean": completed,
    }
    (outdir / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"PB_TURNS={turns}")
    print(f"PB_DIFF_CHARS={len(patch)}")
    print(f"PB_GATE_INTERCEPTS={stats['gate_intercepts']}")
    print(f"PB_GATE_FINAL_STAGE={final_stage}")
    print(f"PB_GATE_COMPLETED={completed}")
    print(f"PB_DELIVERY_CLEAN={completed}")
    print(f"PB_SECONDS={seconds}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SWE-bench 容器内 Agent 运行器")
    parser.add_argument("--dataset", required=True, type=Path, help="含 instance_id/problem_statement 的数据集 JSON")
    parser.add_argument("--instance", required=True, help="实例 id")
    parser.add_argument("--workdir", default="/testbed", help="容器内仓库检出目录")
    parser.add_argument("--venv", default=None, help="testbed 解释器路径（其 bin 目录加入 PATH）")
    parser.add_argument("--mode", choices=["baseline", "gated"], default="baseline")
    parser.add_argument("--label", required=True, help="本次运行的标签（输出子目录名）")
    parser.add_argument("--outdir", required=True, type=Path, help="输出根目录")
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--exec-timeout", type=int, default=300)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
