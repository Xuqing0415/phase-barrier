"""五道防线公共工具（v0.52.0）：证据落盘、脱敏、YAML/JSON 读取。"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..config import GateConfig

# 防线证据统一落在 <workspace>/.agent_gate/defense/ 下：
# 与 state.json / evidence_manifest.json 同级，Agent 侧被禁止写入门禁目录，
# 从而保证“Agent 产生的行为证据”无法事后篡改（与 v0.9.0 证据签名体系一致）。
DEFENSE_DIR_NAME = "defense"

# 敏感内容脱敏（与内置 no_hardcoded_secrets 规则同思路，供 trace 记录复用）
_SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)\b(sk|pk|rk)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key)\s*[=:]\s*[^\s,;}\]]{4,}"),
    re.compile(r"(?i)\b(?:ghp|github_pat_)[A-Za-z0-9_]{20,}\b"),
]


def defense_dir(workspace: Path, config: GateConfig) -> Path:
    """返回防线证据目录并确保存在。"""
    d = (workspace / config.gate_dir_name / DEFENSE_DIR_NAME).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def evidence_path(workspace: Path, config: GateConfig, filename: str) -> Path:
    return defense_dir(workspace, config) / filename


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_evidence(
    workspace: Path, config: GateConfig, filename: str, payload: dict[str, Any]
) -> Path:
    """把防线结果写入门禁目录（原子写）。返回路径。"""
    path = evidence_path(workspace, config, filename)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return path


def read_json(path: Path) -> dict[str, Any] | None:
    if not Path(path).is_file():
        return None
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """以追加模式写 JSONL（供 trace 使用），记录自带脱敏。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not Path(path).is_file():
        return out
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                out.append(data)
    return out


def redact(text: str) -> str:
    """替换敏感内容为 ``***``（供 trace / 证据记录使用）。"""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


def redact_mapping(value: Any) -> Any:
    """递归脱敏任意可 JSON 化的值。"""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {str(k): redact_mapping(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_mapping(v) for v in value]
    return value


def load_yaml_or_json(path: Path) -> dict[str, Any] | None:
    """读取 YAML / JSON 映射文件；不存在或无法解析时返回 None。"""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            if path.suffix.lower() in (".json",):
                data = json.load(fh)
            else:
                data = yaml.safe_load(fh) or {}
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, yaml.YAMLError):
        return None


def _stage_label(stage: int) -> str:
    from ..config import STAGES

    return f"{stage}（{STAGES.get(stage, '?')}）"
