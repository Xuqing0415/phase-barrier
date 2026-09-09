"""防线 3：形式化校验（阶段 1 -> 2，v0.52.0）。

自然语言层面的约束矛盾（如「密码长度 >= 8」与「密码长度 <= 6」同时存在）语义
校验很难稳定识别，但形式化表达后立即可判定。本防线：

1. 从工作区 ``constraints.yaml``（约束 DSL）或手工 ``.tla`` 模块读取硬约束；
2. 对 DSL 做**静态区间矛盾检测**（同一变量的下界 / 上界 / 等值约束求交为空即矛盾），
   无需任何外部工具即可拦截“自相矛盾的 spec”；
3. 若配置并可用 **TLC**（``tlc_bin``），对生成的 TLA+ 模块做模型检查
   （语法 + 不变量校验），覆盖更复杂的时序 / 并发属性；
4. 未提供任何约束文件时降级为「警告不拦截」，保持向后兼容。

DSL 示例（``constraints.yaml``）::

    formal_constraints:
      - id: C1
        description: "密码长度至少 8 位"
        type: range
        variable: password_len
        operator: ">="
        value: 8
      - id: C2
        description: "同一用户并发会话不超过 3"
        type: max_count
        variable: active_sessions
        value: 3
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..config import FormalCheckOptions, GateConfig
from ._base import DefenseCheckResult, DefenseLine
from ._common import evidence_path, now_iso, write_evidence

SUPPORTED_TYPES = ("range", "eq", "neq", "min_count", "max_count")
_COMPARE_OPERATORS = (">=", "<=", ">", "<", "=")


def _parse_constraints(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """解析 DSL，返回 ``(constraints, errors)``。"""
    raw = data.get("formal_constraints", data.get("constraints", []))
    errors: list[str] = []
    constraints: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        errors.append("formal_constraints 必须是列表")
        return constraints, errors
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"第 {i + 1} 条约束不是对象")
            continue
        ctype = item.get("type")
        if ctype not in SUPPORTED_TYPES:
            errors.append(f"约束 {item.get('id', i + 1)} 的 type 不支持: {ctype!r}")
            continue
        cid = str(item.get("id") or f"C{i + 1}")
        variable = str(item.get("variable") or "").strip()
        if not variable:
            errors.append(f"约束 {cid} 缺少 variable")
            continue
        normalized = {
            "id": cid,
            "type": ctype,
            "variable": variable,
            "operator": str(item.get("operator") or ""),
            "value": item.get("value"),
            "description": str(item.get("description") or ""),
        }
        if ctype in ("range",):
            op = normalized["operator"]
            if op not in _COMPARE_OPERATORS:
                errors.append(f"约束 {cid} 的 operator 仅支持 {_COMPARE_OPERATORS}: {op!r}")
                continue
        if normalized["value"] is None:
            errors.append(f"约束 {cid} 缺少 value")
            continue
        constraints.append(normalized)
    return constraints, errors


def detect_static_contradictions(
    constraints: list[dict[str, Any]]
) -> list[str]:
    """对 DSL 做区间求交，返回矛盾描述列表（空列表 = 静态一致）。"""
    contradictions: list[str] = []
    # 每变量维护 [lower, upper] 与 exact 集
    bounds: dict[str, dict[str, Any]] = {}
    for c in constraints:
        var = c["variable"]
        slot = bounds.setdefault(var, {"lower": None, "upper": None, "exact": set(), "neq": set()})
        value = c["value"]
        ctype = c["type"]
        op = c["operator"]
        try:
            if ctype == "eq":
                num = int(value)
                slot["exact"].add(num)
            elif ctype == "neq":
                slot["neq"].add(int(value))
            elif ctype == "range":
                num = int(value)
                if op == ">=":
                    slot["lower"] = num if slot["lower"] is None else max(slot["lower"], num)
                elif op == ">":
                    slot["lower"] = num + 1 if slot["lower"] is None else max(slot["lower"], num + 1)
                elif op == "<=":
                    slot["upper"] = num if slot["upper"] is None else min(slot["upper"], num)
                elif op == "<":
                    slot["upper"] = num - 1 if slot["upper"] is None else min(slot["upper"], num - 1)
                elif op == "=":
                    slot["exact"].add(num)
            elif ctype == "min_count":
                slot["lower"] = int(value) if slot["lower"] is None else max(slot["lower"], int(value))
            elif ctype == "max_count":
                slot["upper"] = int(value) if slot["upper"] is None else min(slot["upper"], int(value))
        except (TypeError, ValueError):
            contradictions.append(f"约束 {c['id']} 的 value 不是整数: {value!r}")

    for var, slot in bounds.items():
        label = f"变量 {var}"
        for exact in slot["exact"]:
            if slot["lower"] is not None and exact < slot["lower"]:
                contradictions.append(f"{label} 矛盾：exact={exact} 与下界 {slot['lower']} 冲突")
            if slot["upper"] is not None and exact > slot["upper"]:
                contradictions.append(f"{label} 矛盾：exact={exact} 与上界 {slot['upper']} 冲突")
        if slot["lower"] is not None and slot["upper"] is not None and slot["lower"] > slot["upper"]:
            contradictions.append(
                f"{label} 区间矛盾：下界 {slot['lower']} > 上界 {slot['upper']}"
            )
        for exact in slot["exact"]:
            if exact in slot["neq"]:
                contradictions.append(f"{label} 矛盾：exact={exact} 同时要求 != {exact}")
    return contradictions


def generate_tla(constraints: list[dict[str, Any]]) -> str:
    """把 DSL 约束渲染为 TLA+ 谓词模块（供 TLC 与审计留痕）。"""
    lines = ["---- MODULE Constraints ----", "EXTENDS Integers", ""]
    for c in constraints:
        var = c["variable"]
        ctype = c["type"]
        value = c["value"]
        if ctype == "range":
            expr = f"{var} {c['operator']} {value}"
        elif ctype == "eq":
            expr = f"{var} = {value}"
        elif ctype == "neq":
            expr = f"{var} /= {value}"
        elif ctype == "min_count":
            expr = f"{var} >= {value}"
        elif ctype == "max_count":
            expr = f"{var} <= {value}"
        else:  # pragma: no cover
            continue
        desc = c.get("description") or ""
        lines.append(f"\\* {c['id']}: {desc}".rstrip())
        lines.append(f"{c['id']} == {expr}")
    cids = [c["id"] for c in constraints]
    if cids:
        lines.append("")
        lines.append("AllConstraints == " + " /\\ ".join(cids))
    lines.append("====")
    return "\n".join(lines) + "\n"


def _find_tlc(bin_path: str | None) -> str | None:
    if bin_path:
        return bin_path if shutil.which(bin_path) else None
    return shutil.which("tlc")


def _run_tlc(tlc: str, module_path: Path, timeout_seconds: float) -> tuple[int, str]:
    proc = subprocess.run(
        [tlc, "-clean", "-terse", str(module_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class FormalCheckLine(DefenseLine):
    """防线 3 实现：阶段 1 -> 2 形式化校验。"""

    name = "formal_check"
    trigger = ((1, 2),)

    def run(
        self,
        workspace: Path,
        config: GateConfig,
        state: Any,
        from_stage: int,
        to_stage: int,
    ) -> DefenseCheckResult:
        opts: FormalCheckOptions = config.defense.formal_check
        cfile = workspace / opts.constraints_file
        hand_tla = cfile.suffix.lower() == ".tla"
        data = None
        if cfile.is_file():
            if hand_tla:
                module_text = cfile.read_text(encoding="utf-8", errors="replace")
                constraints: list[dict[str, Any]] = []
                static_contradictions: list[str] = []
                dsl_errors: list[str] = []
            else:
                import yaml

                try:
                    data = yaml.safe_load(cfile.read_text(encoding="utf-8", errors="replace")) or {}
                except Exception as exc:  # noqa: BLE001
                    data = {}
                    dsl_errors = [f"constraints 文件解析失败: {exc}"]
                constraints, dsl_errors = _parse_constraints(data) if isinstance(data, dict) else ([], [])
                static_contradictions = detect_static_contradictions(constraints)
                module_text = generate_tla(constraints)
        else:
            # 未提供任何约束文件 -> 降级为警告（不拦截），保持向后兼容
            write_evidence(
                workspace,
                config,
                "tla_check.json",
                {
                    "at": now_iso(),
                    "mode": "downgraded",
                    "constraints_file": str(cfile),
                },
            )
            return DefenseCheckResult(
                True,
                "防线 3（形式化校验）：未提供约束文件（"
                f"{opts.constraints_file}），本道防线降级为警告。"
                "建议为硬约束（安全 / 并发 / 边界）补充 DSL 或 .tla",
                {"mode": "downgraded", "constraints_file": str(cfile)},
            )

        tlc = _find_tlc(opts.tlc_bin)
        tlc_result: dict[str, Any] | None = None
        evidence: dict[str, Any] = {
            "at": now_iso(),
            "constraints_file": str(cfile),
            "hand_written_tla": hand_tla,
            "constraints": constraints,
            "dsl_errors": dsl_errors,
            "static_contradictions": static_contradictions,
            "generated_tla": module_text if not hand_tla else None,
        }

        if tlc:
            module_path = evidence_path(workspace, config, "Constraints.tla")
            module_path.write_text(module_text, encoding="utf-8")
            try:
                rc, output = _run_tlc(tlc, module_path, opts.tlc_timeout_seconds)
            except subprocess.TimeoutExpired:
                rc, output = 124, "TLC 模型检查超时"
            except Exception as exc:  # noqa: BLE001
                rc, output = 125, f"TLC 执行异常: {exc}"
            tlc_result = {"returncode": rc, "output_tail": output[-2000:]}
            evidence["tlc"] = tlc_result

        issues: list[str] = []
        issues.extend(dsl_errors)
        if not hand_tla:
            issues.extend(static_contradictions)
        if not hand_tla and not issues and tlc_result is not None and tlc_result["returncode"] != 0:
            issues.append("TLC 模型检查失败（约束可能存在矛盾或语法错误）")
        if hand_tla:
            if tlc_result is None:
                if opts.require_tlc:
                    issues.append("手工 .tla 需要 TLC 校验，但环境中未找到 tlc")
            elif tlc_result["returncode"] != 0:
                issues.append("TLC 对 .tla 模块检查失败")
        if dsl_errors and tlc_result is None and opts.require_tlc:
            issues.append("存在无法静态判定的约束且缺少 TLC，require_tlc=true 拒绝放行")

        if issues:
            write_evidence(workspace, config, "tla_check.json", evidence)
            return DefenseCheckResult(
                False,
                "防线 3（形式化校验）未通过：" + "；".join(issues)
                + "。请先解决约束矛盾再推进阶段 2",
                evidence,
            )
        write_evidence(workspace, config, "tla_check.json", evidence)
        return DefenseCheckResult(
            True,
            "防线 3（形式化校验）通过"
            + ("" if tlc_result is None else f"（TLC returncode={tlc_result['returncode']}）"),
            evidence,
        )
