"""已知残余风险白名单（v1.0.1）。

红队 CI 的口径：**任何逃逸都算失败**，除非该案例被显式列在 ``known_residual.yaml``
里、且豁免尚未过期。白名单是「机制固有窗口」的记账工具，不是免罪符：

- 每条必须写明 ``technique`` / ``case_id``（支持 fnmatch 通配）/ ``reason`` / ``expires_at``；
- 过期即视为漏洞：``--check-residual-expiry`` 会让 CI 失败，强制重新评审；
- 文件本身走代码评审，避免「代码里自己声明预期逃逸」式的自证。
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml

#: 默认白名单文件（与红队技术同目录，便于一起评审）
DEFAULT_RESIDUALS_FILE = Path(__file__).resolve().parent / "known_residual.yaml"


class ResidualConfigError(ValueError):
    """白名单缺失 / 结构非法 / 条目缺字段或日期不合法。"""


@dataclass(frozen=True)
class ResidualWaiver:
    """一条豁免：某个技术的某些案例在 ``expires_at`` 之前允许逃逸。"""

    technique: str
    case_id: str
    reason: str
    expires_at: date

    def matches(self, technique: str, case_id: str) -> bool:
        return fnmatch.fnmatchcase(technique, self.technique) and fnmatch.fnmatchcase(
            case_id, self.case_id
        )

    def expired(self, today: date) -> bool:
        return self.expires_at < today


def _parse_date(value: Any, where: str) -> date:
    if isinstance(value, date):  # yaml 会直接把 YYYY-MM-DD 解析成 date
        return value
    if not isinstance(value, str):
        raise ResidualConfigError(f"{where}: expires_at 必须是 YYYY-MM-DD，得到 {value!r}")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ResidualConfigError(f"{where}: expires_at 不是合法日期：{value!r}") from exc


def load_residuals(path: str | Path | None = None) -> list[ResidualWaiver]:
    """加载并校验白名单；任何结构问题都抛 :class:`ResidualConfigError`。"""
    p = Path(path) if path else DEFAULT_RESIDUALS_FILE
    if not p.is_file():
        raise ResidualConfigError(f"残余风险白名单不存在：{p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ResidualConfigError(f"残余风险白名单无法解析：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ResidualConfigError("残余风险白名单顶层必须是 {version, entries: [...]}")
    waivers: list[ResidualWaiver] = []
    for i, raw in enumerate(data["entries"], 1):
        where = f"第 {i} 条"
        if not isinstance(raw, dict):
            raise ResidualConfigError(f"{where}不是映射")
        missing = [k for k in ("technique", "case_id", "reason", "expires_at") if not raw.get(k)]
        if missing:
            raise ResidualConfigError(f"{where}缺少必填字段：{', '.join(missing)}")
        waivers.append(
            ResidualWaiver(
                technique=str(raw["technique"]),
                case_id=str(raw["case_id"]),
                reason=str(raw["reason"]),
                expires_at=_parse_date(raw["expires_at"], where),
            )
        )
    return waivers


def evaluate_escapes(
    rows: Sequence[dict[str, Any]],
    waivers: Iterable[ResidualWaiver],
    *,
    today: date | None = None,
) -> dict[str, list]:
    """把逃逸行分成「白名单覆盖」与「未豁免」，并单列已过期的豁免条目。

    :return: ``{"waived": [...行], "unwaived": [...行], "expired_waivers": [...豁免]}``
    """
    today = today or date.today()
    waiver_list = list(waivers)
    waived: list[dict[str, Any]] = []
    unwaived: list[dict[str, Any]] = []
    for row in rows:
        if row.get("outcome") != "escaped":
            continue
        technique, case_id = str(row.get("technique", "")), str(row.get("case_id", ""))
        match = next(
            (
                w
                for w in waiver_list
                if w.matches(technique, case_id) and not w.expired(today)
            ),
            None,
        )
        (waived if match else unwaived).append(row)
    expired = [w for w in waiver_list if w.expired(today)]
    return {"waived": waived, "unwaived": unwaived, "expired_waivers": expired}
