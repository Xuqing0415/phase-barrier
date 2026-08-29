"""审计日志：结构化 JSON 日志（优先 structlog，缺省回退 stdlib）。

记录内容：阶段状态变更、工具拦截事件、证据校验结果。
日志文件位于 ``<workspace>/.agent_gate/audit.log``，每个 Skill 实例使用独立
logger（按文件句柄），同一进程内的多个工作区互不串扰。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

try:
    import structlog

    _HAS_STRUCTLOG = True
except ImportError:  # pragma: no cover
    _HAS_STRUCTLOG = False

LOGGER_NAME = "anti_shortcut.audit"


class _JsonFallbackFormatter(logging.Formatter):
    """无 structlog 时的简易 JSON 格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        import json

        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key, value in getattr(record, "payload", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


class _TeeLogger:
    """同时写入文件 logger 与控制台 logger 的简单组合。"""

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


def get_audit_logger(log_file: Path, *, console: bool = False, level: int = logging.INFO):
    """创建审计 logger（独立文件句柄，进程内可安全创建多个）。

    :param log_file: 审计日志文件路径
    :param console: 是否同时输出到控制台
    """
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if _HAS_STRUCTLOG:
        json_processors = [
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]
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
        if console:
            std_logger.addHandler(logging.StreamHandler(sys.stdout))
    std_logger.propagate = False
    return std_logger
