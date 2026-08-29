"""审计日志：结构化 JSON 日志（优先 structlog，缺省回退 stdlib）。

记录内容：阶段状态变更、工具拦截事件、证据校验结果。
日志文件位于 ``<workspace>/.agent_gate/audit.log``，每个 Skill 实例使用独立
logger（按文件句柄），同一进程内的多个工作区互不串扰。

v0.9.0：支持通过 ``remote=RemoteAuditSink`` 把每条审计事件异步转发到
SIEM / HTTP 端点（见 :mod:`anti_shortcut.remote_audit`）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

try:
    import structlog

    _HAS_STRUCTLOG = True
except ImportError:  # pragma: no cover
    _HAS_STRUCTLOG = False

LOGGER_NAME = "anti_shortcut.audit"


def build_payload(record: logging.LogRecord, ts: str | None = None) -> dict[str, Any]:
    """把 stdlib LogRecord 转成结构化 JSON 字段（供文件 / 远程共用）。"""
    payload: dict[str, Any] = {
        "ts": ts or logging.Formatter().formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        "level": record.levelname,
        "event": record.getMessage(),
    }
    for key, value in getattr(record, "payload", {}).items():
        payload[key] = value
    return payload


class _JsonFallbackFormatter(logging.Formatter):
    """无 structlog 时的简单 JSON 格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        import json

        return json.dumps(build_payload(record), ensure_ascii=False)


class _RemoteHandler(logging.Handler):
    """把每条日志异步投递到远程审计端点（远程失败绝不抛出）。"""

    def __init__(self, remote, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._remote = remote

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._remote.enqueue(build_payload(record))
        except Exception:  # pragma: no cover（远程推送绝不能影响主流程）
            pass


class _TeeLogger:
    """同时写入多个 logger 的简单组合（文件 / 控制台 / 远程共用）。"""

    def __init__(self, *loggers) -> None:
        self._loggers = loggers

    def _emit(self, level: str, event, **kwargs):
        for lg in self._loggers:
            getattr(lg, level)(event, **kwargs)

    def info(self, event, **kwargs):
        self._emit("info", event, **kwargs)

    def warning(self, event, **kwargs):
        self._emit("warning", event, **kwargs)

    def error(self, event, **kwargs):
        self._emit("error", event, **kwargs)

    def debug(self, event, **kwargs):
        self._emit("debug", event, **kwargs)


def _remote_processor(remote):
    """structlog processor：在渲染前把事件字典投递到远程端点。"""

    def processor(_logger, _method_name: str, event_dict: dict) -> dict:
        remote.enqueue(dict(event_dict))
        return event_dict

    return processor


def get_audit_logger(
    log_file: Path,
    *,
    console: bool = False,
    level: int = logging.INFO,
    remote=None,
):
    """创建审计 logger（独立文件句柄，进程内可安全创建多个）。

    :param log_file: 审计日志文件路径
    :param console: 是否同时输出到控制台
    :param remote: 可选的 :class:`RemoteAuditSink`，每条事件异步转发到远程端点
    """
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if _HAS_STRUCTLOG:
        json_processors = [
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
        ]
        if remote is not None:
            json_processors.append(_remote_processor(remote))
        json_processors.append(structlog.processors.JSONRenderer(ensure_ascii=False))
        file_logger = structlog.wrap_logger(
            structlog.PrintLogger(file=open(log_file, "a", encoding="utf-8")),
            processors=json_processors,
            wrapper_class=structlog.make_filtering_bound_logger(level),
            cache_logger_on_first_use=True,
        )
        if not console:
            return file_logger
        console_logger = structlog.wrap_logger(
            structlog.PrintLogger(file=sys.stdout),
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.add_log_level,
                structlog.processors.ConsoleRenderer(colors=False),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(level),
            cache_logger_on_first_use=True,
        )
        return _TeeLogger(file_logger, console_logger)

    # 回退：标准库 logging + JSON Formatter
    std_logger = logging.getLogger(f"{LOGGER_NAME}:{log_file}")
    std_logger.setLevel(level)
    if not std_logger.handlers:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(_JsonFallbackFormatter())
        std_logger.addHandler(handler)
        if remote is not None:
            std_logger.addHandler(_RemoteHandler(remote, level=level))
        if console:
            std_logger.addHandler(logging.StreamHandler(sys.stdout))
    std_logger.propagate = False
    return std_logger