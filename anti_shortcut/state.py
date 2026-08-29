"""状态机管理：以 JSON 文件持久化阶段状态与证据，采用原子写入防止损坏。
- 状态文件位于 ``<workspace>/.agent_gate/state.json``（门禁目录）。
- 所有修改必须经过 ``StateManager``，Agent 侧工具被拦截器禁止写入该目录。
- 记录每个阶段的完成历史、证据摘要（文件哈希）、最近一次测试运行结果，
  为“修复后必须重新测试”等校验提供依据。

v0.8.0：HMAC-SHA256 状态签名（``hmac_key`` 或环境变量 ``PHASE_BARRIER_HMAC_KEY``）。
v0.9.0：密钥轮换——``hmac_keys`` 指定轮换期仍接受的旧密钥（验证用），
``rotate_key()`` 用新密钥重新签名；环境变量 ``PHASE_BARRIER_HMAC_KEYS`` 支持
逗号 / 空白分隔的多个旧密钥。未配置密钥时行为与旧版本完全一致。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
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


def _parse_rotation_keys(value: str | None) -> list[str]:
    """解析逗号 / 空白分隔的旧密钥列表（去空）。"""
    if not value:
        return []
    return [k for k in re.split(r"[\s,]+", value) if k]


class StateManager:
    """阶段状态机：负责状态的初始化、原子持久化与阶段推进。"""

    def __init__(
        self,
        state_file: Path,
        *,
        user_request: str = "",
        initial_stage: int = 1,
        hmac_key: str | None = None,
        hmac_keys: list[str] | None = None,
    ) -> None:
        self.state_file = Path(state_file)
        # HMAC-SHA256 状态签名：显式密钥优先，其次环境变量 PHASE_BARRIER_HMAC_KEY
        self._hmac_key = hmac_key or os.environ.get("PHASE_BARRIER_HMAC_KEY")
        if hmac_keys is None:
            hmac_keys = _parse_rotation_keys(os.environ.get("PHASE_BARRIER_HMAC_KEYS"))
        # 轮换期接受的旧密钥（仅用于验证；签名始终使用 _hmac_key）
        self._rotation_keys = [k for k in (hmac_keys or []) if k]
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if self.state_file.exists():
            self._data = self._load()
        else:
            self._data = self._bootstrap(user_request, initial_stage)
            self._atomic_write()
        # 初始启动时确保阶段 0（需求接收）已被记录
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
        if self._hmac_key or self._rotation_keys:
            self._verify_signature(data)
        return data

    def _canonical(self, data: dict) -> bytes:
        """对状态内容做确定性序列化（排除 signature 字段），用于 HMAC 计算。"""
        payload = {k: v for k, v in data.items() if k != "signature"}
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def _sign(self, data: dict, key: str | None = None) -> str:
        key = key or self._hmac_key
        return hmac.new(
            key.encode("utf-8"), self._canonical(data), hashlib.sha256
        ).hexdigest()

    def _verify_signature(self, data: dict) -> None:
        actual = data.get("signature")
        if not isinstance(actual, str):
            raise TamperedStateError(
                "状态文件未签名：配置了 HMAC 密钥但文件中缺少 signature 字段"
                "（文件可能被篡改，或由未启用签名的旧版本生成）"
            )
        candidates: list[str] = []
        if self._hmac_key:
            candidates.append(self._hmac_key)
        candidates.extend(self._rotation_keys)
        for key in candidates:
            expected = "v1:" + self._sign(data, key)
            if hmac.compare_digest(actual, expected):
                return
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

    # ---------- 密钥轮换（v0.9.0） ----------

    def rotate_key(self, new_key: str, *, keep_old: bool = False) -> None:
        """轮换签名密钥：先校验现有签名，再以新密钥重新签名。

        - 状态文件已有签名：必须能被当前密钥集（主密钥 + 轮换密钥）验证，
          否则视为被篡改 / 密钥不匹配，抛出 :class:`TamperedStateError`。
        - 状态文件未签名：视为“启用签名”迁移，直接用新密钥签名。
        - ``keep_old=True`` 时把旧主密钥保留为轮换期验证密钥（宽限期双密钥）。

        :param new_key: 新签名密钥（非空）
        :param keep_old: 是否保留旧密钥进入轮换期
        """
        if not new_key:
            raise ValueError("新密钥不能为空")
        has_signature = isinstance(self._data.get("signature"), str)
        if has_signature:
            if not (self._hmac_key or self._rotation_keys):
                raise TamperedStateError(
                    "状态文件已签名，但未提供任何验证密钥（--from / state_hmac_key / 环境变量）"
                )
            self._verify_signature(self._data)
        if keep_old:
            old = self._hmac_key or ""
            if old and old != new_key:
                self._rotation_keys = [
                    old,
                    *[k for k in self._rotation_keys if k != old],
                ]
        else:
            self._rotation_keys = []
        self._hmac_key = new_key
        self._atomic_write()

    # ---------- 审计辅助 ----------

    def describe(self) -> str:
        return (
            f"current_stage={self.current_stage}({STAGES.get(self.current_stage, '?')}) "
            f"completed={self.completed_stages}"
        )