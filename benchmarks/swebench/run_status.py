"""status.json 的读取与僵尸运行识别。

Scale-50 等长跑编排会在工作目录写 ``status.json``。进程被中断（终端关闭、
OOM、断电）时文件会永远停在 ``running``；读取方若原样相信，就会把已经死掉的
实验当成仍在运行。这里提供两层保护：

- :func:`load_status`：读入并返回规范化副本，不修改磁盘；
- :func:`refresh_status_file`：规范化后把 ``status=stale`` / ``stale_reason``
  写回磁盘，供报告与监控使用。

已知限制：PID 可能被系统复用，单靠 PID 存活不能 100% 证明还是原来那个进程。
若调用方记录了进程启动时间，应自行做二次校验。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

RUNNING = "running"
STALE = "stale"

PidProbe = Callable[[int | None], bool]


def pid_is_alive(pid: int | None) -> bool:
    """判断 PID 是否仍存在（不保证是原进程）。"""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        return _pid_is_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_is_alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    error_access_denied = 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    # 进程可能存在但当前用户无权打开（例如系统进程），按「仍存活」处理更安全。
    return ctypes.get_last_error() == error_access_denied


def normalize_status(
    status: Mapping[str, Any],
    *,
    pid_probe: PidProbe = pid_is_alive,
) -> dict[str, Any]:
    """把「running 但进程已不在」的状态标记为 stale。

    返回新字典，不修改入参。``status`` / ``state`` 两种字段名都支持，
    ``runner_pid`` / ``pid`` 两种 PID 字段名都支持。
    """
    result = dict(status)
    key = "status" if "status" in result else "state"
    if result.get(key) != RUNNING:
        return result
    pid = result.get("runner_pid", result.get("pid"))
    if pid_probe(pid):
        return result
    result[key] = STALE
    result["stale_reason"] = (
        f"runner pid {pid} is not alive" if pid else "runner pid is not recorded"
    )
    return result


def load_status(
    path: str | os.PathLike[str],
    *,
    pid_probe: PidProbe = pid_is_alive,
) -> dict[str, Any]:
    """读取 status.json 并返回规范化副本（不写回）。"""
    data = _read_object(Path(path))
    return normalize_status(data, pid_probe=pid_probe)


def refresh_status_file(
    path: str | os.PathLike[str],
    *,
    pid_probe: PidProbe = pid_is_alive,
) -> dict[str, Any]:
    """规范化并写回 status.json；内容未变化时不重写。"""
    file = Path(path)
    original = _read_object(file)
    normalized = normalize_status(original, pid_probe=pid_probe)
    if normalized != original:
        payload = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
        tmp = file.with_name(file.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8", newline="\n")
        os.replace(tmp, file)
    return normalized


def _read_object(file: Path) -> dict[str, Any]:
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{file} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{file} 顶层必须是对象")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="读取 status.json 并识别僵尸 running 状态")
    parser.add_argument("path")
    parser.add_argument("--write", action="store_true", help="把 stale 标记写回文件")
    args = parser.parse_args(argv)
    try:
        data = refresh_status_file(args.path) if args.write else load_status(args.path)
    except (OSError, ValueError) as exc:
        print(f"读取失败：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(data, ensure_ascii=False, indent=2))
    state = data.get("status", data.get("state"))
    return 1 if state == STALE else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())