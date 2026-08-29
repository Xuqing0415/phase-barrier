"""证据签名清单（v0.9.0）：独立于 state.json 的证据文件哈希 + HMAC 签名。

为什么需要：state.json 被 HMAC 签名只能证明“状态内容没被改”，但如果 Agent 同时
伪造 state.json 并重新 bootstrap，或者事后偷偷替换证据文件，就无从比对。证据清单
把每次阶段推进时校验器看到的证据文件 SHA-256 单独落盘（可选 HMAC 签名），交付 / CI
阶段可用 ``verify-evidence`` 对照工作区当前文件，检测任何事后篡改或文件缺失。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any

from .paths import sha256_file

EVIDENCE_MANIFEST_VERSION = 1
EVIDENCE_MANIFEST_NAME = "evidence_manifest.json"


class EvidenceManifestError(ValueError):
    """证据清单无法解析 / 顶层结构异常 / 版本不兼容。"""


class EvidenceTamperedError(EvidenceManifestError):
    """证据清单签名校验失败：清单可能被篡改，或 HMAC 密钥不匹配。"""


class EvidenceManifest:
    """记录并校验证据文件哈希清单（``.agent_gate/evidence_manifest.json``）。

    :param manifest_file: 清单文件路径（通常为 ``<workspace>/.agent_gate/evidence_manifest.json``）
    :param hmac_key: HMAC-SHA256 密钥；设置后清单写入 ``sig: v1:<hex>`` 并在加载时校验，
                     未设置时清单不签名（仍记录哈希，供文件级比对）。
    """

    def __init__(self, manifest_file: Path, *, hmac_key: str | None = None) -> None:
        self.manifest_file = Path(manifest_file)
        self._hmac_key = hmac_key
        self.manifest_file.parent.mkdir(parents=True, exist_ok=True)
        if self.manifest_file.exists():
            self._data = self._load()
        else:
            self._data = {"version": EVIDENCE_MANIFEST_VERSION, "entries": {}}

    # ---------- 查询 ----------

    def entries(self) -> dict[str, dict[str, Any]]:
        """返回 {相对路径: {stage, sha256}} 的副本。"""
        return {k: dict(v) for k, v in self._data.get("entries", {}).items()}

    def is_signed(self) -> bool:
        return "sig" in self._data

    # ---------- 记录 ----------

    def record(self, stage: int, sha256_map: dict[str, str]) -> None:
        """记录一批证据文件哈希（键为相对工作区路径，值为 SHA-256）。

        同一路径被重复记录时以最新为准（同阶段多次推进只保留最终状态）。
        """
        entries = self._data.setdefault("entries", {})
        for rel, digest in sha256_map.items():
            rel = str(rel).replace("\\", "/").lstrip("/")
            if not rel or not digest:
                continue
            entries[rel] = {"stage": int(stage), "sha256": str(digest)}
        self._atomic_write()

    # ---------- 校验 ----------

    def verify(self, workspace: Path) -> tuple[bool, list[str]]:
        """对照工作区当前文件校验清单，返回 (是否全部通过, 违规列表)。

        违规类型：文件缺失、内容哈希不匹配（sha256 变化即视为被篡改）。
        注意：清单本身是否被篡改在加载时校验（签名失败抛
        :class:`EvidenceTamperedError`），这里只负责文件级比对。
        """
        violations: list[str] = []
        entries = self._data.get("entries", {})
        if not entries:
            violations.append("证据清单为空：尚未记录任何证据文件")
        for rel, meta in sorted(entries.items()):
            target = Path(workspace) / rel
            if not target.is_file():
                violations.append(f"证据文件缺失: {rel}")
                continue
            try:
                current = sha256_file(target)
            except OSError as exc:
                violations.append(f"证据文件不可读: {rel}（{exc}）")
                continue
            if current != meta.get("sha256"):
                violations.append(f"证据文件被篡改: {rel}（sha256 不匹配）")
        return (len(violations) == 0, violations)

    # ---------- 内部 ----------

    def _canonical(self, data: dict) -> bytes:
        payload = {k: v for k, v in data.items() if k != "sig"}
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")

    def _sign(self, data: dict) -> str:
        return hmac.new(
            self._hmac_key.encode("utf-8"), self._canonical(data), hashlib.sha256
        ).hexdigest()

    def _load(self) -> dict:
        try:
            with self.manifest_file.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            raise EvidenceManifestError(
                f"证据清单 {self.manifest_file.name} 无法解析: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise EvidenceManifestError("证据清单顶层结构异常：期望 JSON 对象")
        if data.get("version") != EVIDENCE_MANIFEST_VERSION:
            raise EvidenceManifestError(
                f"证据清单版本不兼容: {data.get('version')} != {EVIDENCE_MANIFEST_VERSION}"
            )
        if self._hmac_key and "sig" not in data:
            raise EvidenceTamperedError(
                "证据清单未签名：配置了 HMAC 密钥但清单中缺少 sig 字段"
                "（清单可能被篡改，或由未启用签名的旧版本生成）"
            )
        if self._hmac_key:
            expected = "v1:" + self._sign(data)
            actual = data.get("sig")
            if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
                raise EvidenceTamperedError(
                    "证据清单签名校验失败：清单可能被篡改，或 HMAC 密钥不匹配"
                )
        return data

    def _atomic_write(self) -> None:
        if self._hmac_key:
            self._data["sig"] = "v1:" + self._sign(self._data)
        tmp = self.manifest_file.with_suffix(self.manifest_file.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.manifest_file)


__all__ = [
    "EVIDENCE_MANIFEST_NAME",
    "EVIDENCE_MANIFEST_VERSION",
    "EvidenceManifest",
    "EvidenceManifestError",
    "EvidenceTamperedError",
]