"""远程审计推送（SIEM）：把结构化 JSON 审计事件异步批量转发到 HTTP 端点。

设计要点：

- 零额外依赖：仅用标准库 ``urllib.request`` + ``threading`` + ``queue``。
- 异步批量：后台线程把队列中的事件按“条数上限”攒批后 POST JSON。
- 绝不阻塞门禁主流程：网络失败只计数丢弃，不影响阶段校验。
- 队列有界：超过 ``max_queue`` 时丢弃最旧事件（drop-oldest），避免内存无限增长。
- ``flush()`` 是确定性的：同步排空队列，并等待后台线程把手头批次发完。
- 单事件发送单对象，多事件发送 JSON 数组，方便对接 ELK / Loki 等收集端。

v0.10.0 增强：
- TLS 自定义 CA：``ca_bundle`` 指定 PEM 证书文件，用于自建 SIEM 的 HTTPS 端点。
- 失败重试：``retries`` 次指数退避重试（``backoff_factor * 2**attempt`` 秒）。
"""
from __future__ import annotations

import json
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any


class RemoteAuditSink:
    """把审计事件异步 POST 到远端（SIEM / webhook）的推送器。"""

    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        timeout: float = 5.0,
        batch_size: int = 50,
        max_queue: int = 1000,
        flush_interval: float = 5.0,
        start_worker: bool = True,
        ca_bundle: str | None = None,
        retries: int = 2,
        backoff_factor: float = 0.5,
    ) -> None:
        if not url:
            raise ValueError("audit_remote_url 不能为空")
        self.url = url
        self.token = token
        self.timeout = max(0.1, float(timeout))
        self.batch_size = max(1, int(batch_size))
        self.max_queue = max(1, int(max_queue))
        self.flush_interval = max(0.1, float(flush_interval))
        self.retries = max(0, int(retries))
        self.backoff_factor = max(0.0, float(backoff_factor))
        self.ca_bundle = ca_bundle
        # 自定义 CA：启动时即校验并构建 HTTPS opener（配置错误尽快暴露）
        self._opener = self._build_opener()
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=self.max_queue)
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._busy = False  # 后台线程是否持有未发送的批次
        self._enqueued = 0
        self._dropped = 0
        self._sent_events = 0
        self._sent_batches = 0
        self._failed_batches = 0
        self._worker: threading.Thread | None = None
        if start_worker:
            self._worker = threading.Thread(
                target=self._run, name="phase-barrier-audit-sink", daemon=True
            )
            self._worker.start()

    def _build_opener(self):
        """根据 ca_bundle 构建支持自定义 CA 的 HTTPS opener；未配置时返回 None。"""
        if not self.ca_bundle:
            return None
        ca = Path(self.ca_bundle)
        if not ca.is_file():
            raise ValueError(f"audit_remote_ca_bundle 文件不存在: {ca}")
        try:
            context = ssl.create_default_context(cafile=str(ca))
        except (ssl.SSLError, OSError, ValueError) as exc:
            raise ValueError(f"audit_remote_ca_bundle 无法加载: {exc}") from exc
        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context)
        )

    # ---------- 统计 ----------

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "enqueued": self._enqueued,
                "dropped": self._dropped,
                "sent_events": self._sent_events,
                "sent_batches": self._sent_batches,
                "failed_batches": self._failed_batches,
                "queued": self._queue.qsize(),
                "busy": self._busy,
            }

    # ---------- 入队 ----------

    def enqueue(self, payload: dict[str, Any]) -> None:
        """投递一条审计事件；队列满时丢弃最旧事件并计数，绝不抛出。"""
        if self._closed.is_set():
            return
        event = dict(payload)
        try:
            self._queue.put_nowait(event)
        except Full:
            # drop-oldest：腾出位置给最新事件
            try:
                self._queue.get_nowait()
            except Empty:
                pass
            try:
                self._queue.put_nowait(event)
            except Full:  # pragma: no cover（极端竞态兜底）
                with self._lock:
                    self._dropped += 1
                return
            with self._lock:
                self._dropped += 1
        with self._lock:
            self._enqueued += 1

    def flush(self, timeout: float = 5.0) -> None:
        """同步发送当前队列中的全部事件（供测试 / 关闭前调用）。

        先把队列排空发送，再等待后台线程把手头批次发完（最多等待 ``timeout`` 秒），
        保证返回时所有“已入队”事件都已尝试发送。
        """
        if self._closed.is_set():
            return
        deadline = time.monotonic() + timeout
        while True:
            self._send_drained()
            with self._lock:
                if self._queue.empty() and not self._busy:
                    return
            if time.monotonic() >= deadline:
                return
            time.sleep(0.005)

    def close(self, timeout: float = 5.0) -> None:
        """关闭推送器：冲刷剩余事件并停止后台线程（幂等）。"""
        if self._closed.is_set():
            return
        self._closed.set()
        if self._worker is not None:
            self._worker.join(timeout=timeout)
        self.flush(timeout=timeout)

    # ---------- 发送 ----------

    def _send_drained(self) -> None:
        """把当前队列中的事件同步排空发送（批次上限内）。"""
        while True:
            batch: list[dict[str, Any]] = []
            while len(batch) < self.batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except Empty:
                    break
            if not batch:
                return
            self._send_batch(batch)

    def _send_batch(self, batch: list[dict[str, Any]]) -> None:
        """发送一个批次；失败时按指数退避重试，仍失败则计数丢弃。"""
        if not batch:
            return
        body = batch[0] if len(batch) == 1 else batch
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        for attempt in range(self.retries + 1):
            try:
                if self._opener is not None:
                    with self._opener.open(request, timeout=self.timeout) as resp:
                        resp.read()
                else:
                    with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                        resp.read()
            except (urllib.error.URLError, OSError, ValueError):
                if attempt < self.retries and self.backoff_factor > 0:
                    time.sleep(self.backoff_factor * (2 ** attempt))
                continue
            with self._lock:
                self._sent_batches += 1
                self._sent_events += len(batch)
            return
        with self._lock:
            self._failed_batches += 1

    # ---------- 后台工作线程 ----------

    def _run(self) -> None:
        poll = min(0.2, self.flush_interval)
        while not self._closed.is_set():
            with self._lock:
                self._busy = True
            try:
                item = self._queue.get(timeout=poll)
            except Empty:
                with self._lock:
                    self._busy = False
                continue
            batch = [item]
            while len(batch) < self.batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except Empty:
                    break
            self._send_batch(batch)
            with self._lock:
                self._busy = False
        # 关闭后：把剩余事件全部发送
        while True:
            with self._lock:
                self._busy = True
            batch: list[dict[str, Any]] = []
            while len(batch) < self.batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except Empty:
                    break
            if not batch:
                with self._lock:
                    self._busy = False
                break
            self._send_batch(batch)
            with self._lock:
                self._busy = False


__all__ = ["RemoteAuditSink"]