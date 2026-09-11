"""SWE-bench 宿主侧评分器（官方 swebench harness，2026-09）。

在宿主用 ``swebench==5.0.2`` 的官方 harness 对 Agent 产出的补丁打分：把补丁送进该实例的
官方评测镜像，应用后运行 ``/eval.sh``，按 FAIL_TO_PASS / PASS_TO_PASS 判定 resolved。

契约（与 ``scripts/run_swebench_batch_container.py`` 对齐）::

    <grade-python> grade.py --instance IID --dataset DS.json --patch-file P.diff \
        --label L --out-dir EVAL_RUNS --timeout 1800

- 退出码：``0`` = resolved，``2`` = unresolved，``3`` = 评分过程出错
- stdout 标记：``PB_RESOLVED=1|0``、``PB_GRADE_SUMMARY=<json>``
- 日志：``{out-dir}/logs/run_evaluation/{run_id}/{model}/{instance}/``

数据集须是官方行结构（含 ``instance_id`` / ``image`` / ``eval_script`` / ``FAIL_TO_PASS`` 等
列），例如从 ``SWE-bench/SWE-bench_Verified`` 导出的 JSON。注意 ``princeton-nlp/SWE-bench_Verified``
缺 ``eval_script`` 列，会让 ``make_test_spec`` 抛 ``KeyError``。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def force_lf_writes():
    """在评分进程内强制 ``Path.write_text`` 写 LF。

    Windows 上 ``Path.write_text`` 默认把 ``\n`` 翻成 ``\r\n``。官方 harness 会把
    数据集里的 ``eval_script`` 先写成宿主 ``eval.sh``、再拷贝进容器；CRLF 会让容器内
    ``set -e`` / ``conda activate`` / ``cd /testbed`` 全部解析失败，表现为「所有
    PASS_TO_PASS 全挂」的假阴性。同样，``patch.diff`` 带 CR 也会影响 git apply。
    Linux 上本就是 LF，无副作用。
    """
    original = Path.write_text

    def _write_text(self, data, encoding=None, errors=None, newline="\n"):
        return original(self, data, encoding=encoding or "utf-8", errors=errors, newline=newline)

    Path.write_text = _write_text
    try:
        yield
    finally:
        Path.write_text = original


def load_rows(path: Path) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("instances", "data", "rows", "tasks"):
            if isinstance(raw.get(key), list):
                return raw[key]
    raise SystemExit(f"{path}: 无法识别数据集结构")


def main() -> int:
    parser = argparse.ArgumentParser(description="SWE-bench 官方 harness 评分")
    parser.add_argument("--instance", required=True)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--patch-file", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--model-name", default="phase-barrier")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    dataset = str(args.dataset.resolve())
    patch_path = args.patch_file.resolve()

    if not patch_path.exists() or patch_path.stat().st_size == 0:
        print("PB_RESOLVED=0")
        print("PB_GRADE_SUMMARY=" + json.dumps({"error": "empty_patch"}, ensure_ascii=False))
        return 2

    rows = [row for row in load_rows(args.dataset)
            if str(row.get("instance_id")) == args.instance]
    if not rows:
        print(f"数据集 {dataset} 中找不到实例 {args.instance}", file=sys.stderr)
        return 3
    instance = rows[0]
    patch_text = patch_path.read_text(encoding="utf-8", errors="replace")

    # run_id 里带上补丁内容哈希：官方 harness 会按 run_id/model/instance 缓存
    # report.json，命中缓存就直接返回——若沿用同一个 run_id，换一份补丁也会拿到上一次的
    # 结论（实测会导致「新补丁被判成旧补丁的结果」的假阴性）。带上哈希后，同一补丁可复用
    # 结果（幂等、快），不同补丁必然重新评测。
    patch_digest = hashlib.sha256(patch_text.encode("utf-8")).hexdigest()[:12]
    run_id = args.run_id or f"{args.label}-{patch_digest}"
    predictions_dir = args.out_dir / "predictions"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = predictions_dir / f"{args.label}.json"
    prediction = {
        "instance_id": args.instance,
        "model_name_or_path": args.model_name,
        "model_patch": patch_text,
    }
    predictions_path.write_text(json.dumps([prediction], ensure_ascii=False, indent=2),
                                encoding="utf-8")

    # 官方 harness 把日志写到相对路径 logs/run_evaluation/...；切到 out_dir 让其落盘在此
    os.chdir(args.out_dir)

    try:
        import docker
        from swebench.harness.run_evaluation import run_instance
        from swebench.harness.utils import make_test_spec
    except ImportError as exc:  # pragma: no cover - 环境缺失时给出明确提示
        print(f"评分环境缺少 swebench/docker：{exc}", file=sys.stderr)
        return 3

    try:
        with force_lf_writes():
            test_spec = make_test_spec(instance)
            client = docker.from_env()
            result = run_instance(
                test_spec=test_spec,
                pred=prediction,
                client=client,
                run_id=run_id,
                timeout=args.timeout,
                rewrite_reports=False,
            )
    except Exception as exc:  # noqa: BLE001 - 评分失败按 unresolved 记录，不拖垮整批
        print(f"评分异常: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("PB_RESOLVED=0")
        print("PB_GRADE_SUMMARY=" + json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 3

    if not result:
        print("PB_RESOLVED=0")
        print("PB_GRADE_SUMMARY=" + json.dumps({"error": "harness_returned_none"}, ensure_ascii=False))
        return 3

    _instance_id, report = result
    entry = (report or {}).get(args.instance) or {}
    resolved = bool(entry.get("resolved"))
    status = entry.get("tests_status") or {}
    summary = {
        "resolved": resolved,
        "f2p": {k: len(v) for k, v in (status.get("FAIL_TO_PASS") or {}).items()},
        "p2p": {k: len(v) for k, v in (status.get("PASS_TO_PASS") or {}).items()},
    }
    report_path = args.out_dir / "reports" / f"{args.label}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"PB_RESOLVED={1 if resolved else 0}")
    print("PB_GRADE_SUMMARY=" + json.dumps(summary, ensure_ascii=False))
    return 0 if resolved else 2


if __name__ == "__main__":
    raise SystemExit(main())
