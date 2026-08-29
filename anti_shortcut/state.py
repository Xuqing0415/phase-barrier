"""状态机管理：以 JSON 文件持久化阶段状态与证据，采用原子写入防止损坏。

- 状态文件位于 ``<workspace>/.agent_gate/state.json``（门禁目录）。
- 所有修改必须经过 ``StateManager``，Agent 侧工具被拦截器禁止写入该目录。
- 记录每个阶段的完成历史、证据摘要（文件哈希）、最近一次测试运行结果，
  为“修复后必须重新测试”等校验提供依据。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import STAGES

STATE_VERSION = 1


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
    ) -> None:
        self.state_file = Path(state_file)
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
        with self.state_file.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("version") != STATE_VERSION:
            raise ValueError(f"状态文件版本不兼容: {data.get('version')} != {STATE_VERSION}")
        return data

    def _atomic_write(self) -> None:
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
