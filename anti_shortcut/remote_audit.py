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

v0.11.0 增强：
- mTLS 客户端证书：``client_cert`` / ``client_key`` 指定 PEM 文件，用于双向 TLS 端点。
- 自定义请求头：``headers`` 合并到每次 POST（token 仍以 ``Authorization`` 优先）。
- 持久化重试队列：``spool_dir`` 指定目录后，重试耗尽的批次落盘为 JSONL，
  进程重启时自动恢复重新发送，避免进程崩溃 / 滚动重启丢事件。
"""
from __future__ import annotations

import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

# 持久化重试队列文件名（JSON Lines：每行一个审计事件）
_SPOOL_FILE = "audit_spool.jsonl"


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
        client_cert: str | None = None,
        client_key: str | None = None,
        headers: dict[str, str] | None = None,
        spool_dir: str | None = None,
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
        self.client_cert = client_cert
        self.client_key = client_key
        self.headers = dict(headers or {})
        self.spool_dir = spool_dir
        # 自定义 CA / mTLS 客户端证书：启动时即校验并构建 HTTPS opener（配置错误尽快暴露）
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
        self._spooled_events = 0
        self._recovered_events = 0
        self._worker: threading.Thread | None = None
        # 持久化重试队列：字段初始化完成后恢复上次未发送成功的事件
        self._recover_spool()
        if start_worker:
            self._worker = threading.Thread(
                target=self._run, name="phase-barrier-audit-sink", daemon=True
            )
            self._worker.start()

    def _build_opener(self):
        """根据 ca_bundle / 客户端证书构建 HTTPS opener；均未配置时返回 None。"""
        if not self.ca_bundle and not self.client_cert and not self.client_key:
            return None
        if self.ca_bundle:
            ca = Path(self.ca_bundle)
            if not ca.is_file():
                raise ValueError(f"audit_remote_ca_bundle 文件不存在: {ca}")
            try:
                context = ssl.create_default_context(cafile=str(ca))
            except (ssl.SSLError, OSError, ValueError) as exc:
                raise ValueError(f"audit_remote_ca_bundle 无法加载: {exc}") from exc
        else:
            context = ssl.create_default_context()
        self._load_client_cert(context)
        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context)
        )

    def _load_client_cert(self, context: ssl.SSLContext) -> None:
        """把 mTLS 客户端证书链加载进 SSLContext（证书 / 私钥成对校验）。"""
        if not self.client_cert and not self.client_key:
            return
        if not self.client_cert or not self.client_key:
            raise ValueError("audit_remote_client_cert 与 audit_remote_client_key 必须成对配置")
        cert = Path(self.client_cert)
        key = Path(self.client_key)
        if not cert.is_file():
            raise ValueError(f"audit_remote_client_cert 文件不存在: {cert}")
        if not key.is_file():
            raise ValueError(f"audit_remote_client_key 文件不存在: {key}")
        try:
            context.load_cert_chain(certfile=str(cert), keyfile=str(key))
        except (ssl.SSLError, OSError, ValueError) as exc:
            raise ValueError(f"audit_remote_client_cert 无法加载: {exc}") from exc

    # ---------- 统计 ----------

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "enqueued": self._enqueued,
                "dropped": self._dropped,
                "sent_events": self._sent_events,
                "sent_batches": self._sent_batches,
                "failed_batches": self._failed_batches,
                "spooled_events": self._spooled_events,
                "recovered_events": self._recovered_events,
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
        headers = {"Content-Type": "application/json", **self.headers}
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
        # v0.11.0：持久化重试队列——重试耗尽后落盘，进程重启时恢复重发
        self._spool_batch(batch)

    # ---------- 持久化重试队列（spool） ----------

    def _recover_spool(self) -> None:
        """启动时把上次遗留的 spool 事件重新入队（读后删除，失败则忽略）。"""
        if not self.spool_dir:
            return
        spool = Path(self.spool_dir) / _SPOOL_FILE
        if not spool.is_file():
            return
        recovered = 0
        try:
            with spool.open("r", encoding="utf-8") as fh:
                for ln in fh:
                    line = ln.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        self.enqueue(event)
                        recovered += 1
            spool.unlink(missing_ok=True)
        except OSError:
            return  # spool 不可读时静默跳过，避免启动失败
        with self._lock:
            self._recovered_events += recovered

    def _spool_batch(self, batch: list[dict[str, Any]]) -> None:
        """把一个发送失败的批次追加到 spool（JSONL）；写入失败只丢弃，不抛出。"""
        if not self.spool_dir or not batch:
            return
        spool = Path(self.spool_dir) / _SPOOL_FILE
        try:
            spool.parent.mkdir(parents=True, exist_ok=True)
            with spool.open("a", encoding="utf-8") as fh:
                for event in batch:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            return
        with self._lock:
            self._spooled_events += len(batch)

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