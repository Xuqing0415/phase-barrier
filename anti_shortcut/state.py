"""状态机管理：以 JSON 文件持久化阶段状态与证据，采用原子写入防止损坏。

- 状态文件位于 ``<workspace>/.agent_gate/state.json``（门禁目录）。
- 所有修改必须经过 ``StateManager``，Agent 侧工具被拦截器禁止写入该目录。
- 记录每个阶段的完成历史、证据摘要（文件哈希）、最近一次测试运行结果，
  为“修复后必须重新测试”等校验提供依据。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import STAGES

STATE_VERSION = 1


class CorruptedStateError(ValueError):
    """状态文件无法解析 / 顶层结构异常 / 版本不兼容（损坏或被篡改）。"""


class TamperedStateError(CorruptedStateError):
    """状态文件签名校验失败：文件可能被篡改，或 HMAC 密钥不匹配。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateManager:
    """阶段状态机：负责状态的初始化、原子持久化与阶段推进。"""

    def __init__(
        self,
        state_file: Path,
        *,
        user_request: str = "",
        initial_stage: int = 1,
        hmac_key: str | None = None,
    ) -> None:
        self.state_file = Path(state_file)
        # HMAC-SHA256 状态签名（v0.8.0）：显式密钥优先，其次环境变量
        self._hmac_key = hmac_key or os.environ.get("PHASE_BARRIER_HMAC_KEY")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if self.state_file.exists():
            self._data = self._load()
        else:
            self._data = self._bootstrap(user_request, initial_stage)
            self._atomic_write()
        # 初次启动时确保阶段 0（需求接收）已被记录
        if user_request and not self.get_evidence("user_request"):
            self.record_user_request(user_request)

    # ---------- 基础读写 ----------

    def _bootstrap(self, user_request: str, initial_stage: int) -> dict:
        now = _now_iso()
        return {
            "version": STATE_VERSION,
            "current_stage": initial_stage,
            "completed_stages": [0],
            "stage_history": [
                {
                    "stage": 0,
                    "name": STAGES[0],
                    "timestamp": now,
                    "evidence": {"user_request": user_request},
                }
            ],
            "evidence": {
                "user_request": user_request,
                "spec": {},
                "tests": {},
                "implementation": {},
                "last_test_run": {},
                "last_source_change_at_epoch": None,
            },
        }

    def _load(self) -> dict:
        try:
            with self.state_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            raise CorruptedStateError(
                f"状态文件 {self.state_file.name} 无法解析: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise CorruptedStateError(
                f"状态文件 {self.state_file.name} 顶层结构异常: 期望 JSON 对象"
            )
        if data.get("version") != STATE_VERSION:
            raise CorruptedStateError(
                f"状态文件版本不兼容: {data.get('version')} != {STATE_VERSION}"
            )
        if self._hmac_key:
            self._verify_signature(data)
        return data

    def _canonical(self, data: dict) -> bytes:
        """对状态内容做确定性序列化（排除 signature 字段），用于 HMAC 计算。"""
        payload = {k: v for k, v in data.items() if k != "signature"}
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def _sign(self, data: dict) -> str:
        return hmac.new(
            self._hmac_key.encode("utf-8"), self._canonical(data), hashlib.sha256
        ).hexdigest()

    def _verify_signature(self, data: dict) -> None:
        if "signature" not in data:
            raise TamperedStateError(
                "状态文件未签名：配置了 HMAC 密钥但文件中缺少 signature 字段"
                "（文件可能被篡改，或由未启用签名的旧版本生成）"
            )
        expected = "v1:" + self._sign(data)
        actual = data.get("signature")
        if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
            raise TamperedStateError(
                "状态文件签名校验失败：文件可能被篡改，或 HMAC 密钥不匹配"
            )

    def _atomic_write(self) -> None:
        if self._hmac_key:
            self._data["signature"] = "v1:" + self._sign(self._data)
        tmp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_file)

    def snapshot(self) -> dict:
        """返回状态的只读快照（供校验器 / 审计使用）。"""
        import copy

        return copy.deepcopy(self._data)

    # ---------- 查询 ----------

    @property
    def current_stage(self) -> int:
        return int(self._data["current_stage"])

    @property
    def completed_stages(self) -> list[int]:
        return list(self._data["completed_stages"])

    @property
    def is_complete(self) -> bool:
        return self.current_stage >= 6

    def get_evidence(self, key: str, default=None):
        return self._data["evidence"].get(key, default)

    # ---------- 修改 ----------

    def record_user_request(self, request: str) -> None:
        self._data["evidence"]["user_request"] = request
        if self._data["stage_history"] and self._data["stage_history"][0]["stage"] == 0:
            self._data["stage_history"][0]["evidence"]["user_request"] = request
        self._atomic_write()

    def advance(self, new_stage: int, evidence: dict | None = None) -> None:
        cur = self.current_stage
        if new_stage != cur + 1 and not (cur == 4 and new_stage in (5, 6)):
            raise ValueError(f"不允许跳跃阶段: {cur} -> {new_stage}")
        self._data["current_stage"] = new_stage
        self._data["completed_stages"].append(cur)
        now = _now_iso()
        self._data["stage_history"].append(
            {
                "stage": cur,
                "name": STAGES.get(cur, str(cur)),
                "timestamp": now,
                "evidence": evidence or {},
            }
        )
        self._atomic_write()

    def set_evidence(self, key: str, value) -> None:
        self._data["evidence"][key] = value
        self._atomic_write()

    def mark_source_change(self, path: str) -> None:
        """记录代码/测试文件发生变更的时间戳（用于“修复后必须重测”校验）。"""
        self._data["evidence"]["last_source_change_at_epoch"] = time.time()
        self._data["evidence"]["last_source_change_path"] = path
        self._atomic_write()

    def mark_test_run(self, result: dict) -> None:
        """记录最近一次测试运行结果（退出码、是否通过、输出摘要、时间戳）。"""
        result = dict(result)
        result.setdefault("at_epoch", time.time())
        result.setdefault("at", _now_iso())
        self._data["evidence"]["last_test_run"] = result
        self._atomic_write()

    # ---------- 审计辅助 ----------

    def describe(self) -> str:
        return (
            f"current_stage={self.current_stage}({STAGES.get(self.current_stage, '?')}) "
            f"completed={self.completed_stages}"
        )
