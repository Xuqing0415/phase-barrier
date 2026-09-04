"""透明代理引擎（v0.17.0）。

K8s sidecar 模式的核心：Agent 容器不挂载门禁目录、不信任自身工具包装，
所有文件写入与命令执行都走 sidecar 的 ``/api/write`` 与 ``/api/exec`` 代理端点。
本模块封装“同一套阶段门禁策略”的远程执行实现：

- 写入：路径限定在工作区内、拒绝门禁目录 ``.agent_gate``、按阶段拦截
  test / source / other 文件类型（与 ``AntiShortcutSkill.wrap_write_file`` 同策略）。
- 执行：按阶段拦截测试命令、拦截访问门禁目录的 shell 命令（与
  ``AntiShortcutSkill.wrap_execute_command`` 同策略），执行后自动记录测试摘要。
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any

from .interceptors import is_language_test_command, summarize_test_output
from .skill import AntiShortcutSkill

DEFAULT_EXEC_TIMEOUT = 120
MAX_EXEC_TIMEOUT = 3600


class ProxyError(Exception):
    """代理参数非法或路径越界（HTTP 400）。"""


class WriteDenied(ProxyError):
    """写入被阶段门禁拒绝（HTTP 403）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ExecDenied(ProxyError):
    """命令执行被阶段门禁拒绝（HTTP 403）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GateProxy:
    """基于同一 ``AntiShortcutSkill`` 的透明代理：写入与命令执行都先过门禁。"""

    def __init__(self, skill: AntiShortcutSkill) -> None:
        self.skill = skill

    # ---------- 路径解析 ----------

    def resolve_write_path(self, path: str | Path) -> Path:
        """把（相对/绝对）路径解析为工作区内的绝对路径；越界抛 ProxyError。"""
        if not isinstance(path, (str, Path)):
            raise ProxyError("path 必须是字符串")
        raw = str(path).strip()
        if not raw:
            raise ProxyError("path 不能为空")
        p = Path(raw)
        if not p.is_absolute():
            p = self.skill.workspace / p
        try:
            resolved = p.resolve(strict=False)
        except OSError as exc:
            raise ProxyError(f"无法解析路径: {raw}") from exc
        try:
            resolved.relative_to(self.skill.workspace)
        except ValueError:
            raise ProxyError(f"路径越出工作区: {raw}")
        if resolved == self.skill.workspace:
            raise ProxyError("不能把工作区根目录当作文件写入")
        return resolved

    def _resolve_cwd(self, cwd: str | Path | None) -> Path:
        if cwd is None:
            return self.skill.workspace
        if not isinstance(cwd, (str, Path)):
            raise ProxyError("cwd 必须是字符串")
        raw = str(cwd).strip()
        if not raw:
            return self.skill.workspace
        p = Path(raw)
        if not p.is_absolute():
            p = self.skill.workspace / p
        resolved = p.resolve(strict=False)
        try:
            resolved.relative_to(self.skill.workspace)
        except ValueError:
            raise ProxyError(f"cwd 越出工作区: {raw}")
        return resolved

    # ---------- 写入 ----------

    def write_file(self, path: str | Path, content: str) -> dict[str, Any]:
        """经门禁写入文件；被拒绝抛 WriteDenied，参数非法抛 ProxyError。"""
        target = self.resolve_write_path(path)
        if target.is_dir():
            raise ProxyError(f"目标路径是目录，不能写入: {target}")
        if not isinstance(content, str):
            raise ProxyError("content 必须是字符串")
        try:
            self.skill.check_write_permission(target)
        except PermissionError as exc:
            self.skill.logger.warning(
                "proxy_write_denied",
                path=str(target),
                reason=str(exc),
                **self.skill._stage_summary(),
            )
            raise WriteDenied(str(exc)) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        kind = self.skill._classify_path(target)
        if kind in ("test", "source"):
            self.skill.state.mark_source_change(str(target))
        self.skill.logger.info(
            "proxy_write_ok",
            path=str(target),
            kind=kind,
            **self.skill._stage_summary(),
        )
        return {"ok": True, "path": str(target), "kind": kind}

    # ---------- 命令执行 ----------

    def execute_command(
        self,
        command: str,
        *,
        cwd: str | Path | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """经门禁执行 shell 命令；被拒绝抛 ExecDenied，参数非法抛 ProxyError。

        返回 ``{"ok": True, "exit_code": int, "output": str, "recorded_test_run": bool}``。
        退出码非 0 不抛错——与 Agent 工具约定一致，测试失败由状态机记录。
        超时（v0.17.0 修复）：主动终止整个进程树，避免孤儿子进程占用管道导致
        调用方长时间阻塞。
        """
        if not isinstance(command, str) or not command.strip():
            raise ProxyError("command 必须是非空字符串")
        if timeout is not None and (
            not isinstance(timeout, int) or not 1 <= timeout <= MAX_EXEC_TIMEOUT
        ):
            raise ProxyError(f"timeout 必须是 1-{MAX_EXEC_TIMEOUT} 的整数秒")
        try:
            self.skill.check_exec_permission(command)
        except PermissionError as exc:
            self.skill.logger.warning(
                "proxy_exec_denied",
                command=command,
                reason=str(exc),
                **self.skill._stage_summary(),
            )
            raise ExecDenied(str(exc)) from exc
        run_dir = self._resolve_cwd(cwd)
        eff_timeout = timeout or DEFAULT_EXEC_TIMEOUT
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=str(run_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except OSError as exc:
            # v0.35.0：启动失败（如 cwd 不存在 / 权限受限）转为 ProxyError（HTTP 400），
            # 而不是让裸异常打到 HTTP 层导致 500 / 连接断开。
            self.skill.logger.warning(
                "proxy_exec_start_failed",
                command=command,
                reason=str(exc),
                **self.skill._stage_summary(),
            )
            raise ProxyError(f"命令无法启动: {exc}") from exc
        try:
            stdout, stderr = proc.communicate(timeout=eff_timeout)
        except subprocess.TimeoutExpired:
            # 超时：尽力终止整个进程树后立即返回，不等待孤儿进程释放管道
            # （Windows 下等待管道 EOF 会被仍在运行的孙进程阻塞到其自然退出）。
            _terminate_process_tree(proc)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            self.skill.logger.warning(
                "proxy_exec_timeout",
                command=command,
                timeout=eff_timeout,
                **self.skill._stage_summary(),
            )
            return {
                "ok": True,
                "exit_code": -1,
                "output": f"命令执行超时（>{eff_timeout}s）",
                "recorded_test_run": False,
                "timed_out": True,
            }
        output = (stdout or "") + (stderr or "")
        recorded = False
        if is_language_test_command(command, self.skill.config, self.skill.adapter):
            record = summarize_test_output(output, proc.returncode, adapter=self.skill.adapter)
            visible = {k: v for k, v in record.items() if k != "output_tail"}
            self.skill.state.mark_test_run(visible)
            recorded = True
        self.skill.logger.info(
            "proxy_exec_ok",
            command=command,
            exit_code=proc.returncode,
            recorded_test_run=recorded,
            **self.skill._stage_summary(),
        )
        return {
            "ok": True,
            "exit_code": proc.returncode,
            "output": output,
            "recorded_test_run": recorded,
        }


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """尽力终止进程树（Windows 优先 taskkill /T，失败回退直接 kill；POSIX 用进程组信号）。

    注意：不保证能杀死脱离进程树的孙进程（如 Windows 沙箱环境拒绝 taskkill），
    但调用方不会等待其退出，因此不影响超时响应的及时性。
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
