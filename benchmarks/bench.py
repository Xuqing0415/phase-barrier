"""phase-barrier 性能基准（v0.35.0）。

衡量两个热点路径：
1. ``StateManager`` 并发状态写入：多 Agent 共享同一 ``state.json`` 时的
   文件锁 + 写前重载 + 原子写 + HMAC 签名吞吐与延迟（v0.26.3 多 Agent 场景）。
2. ``GateSidecar`` HTTP 透明代理：并发 ``write_file`` / ``execute_command``
   经门禁检查的端到端延迟（v0.17.0 场景）。

用法：
  python benchmarks/bench.py                 # 运行全部基准，控制台表格
  python benchmarks/bench.py --json          # 仅输出 JSON 到 stdout
  python benchmarks/bench.py --output out.json   # 结果另存 JSON
  python benchmarks/bench.py --fail-fast     # 任一指标超阈值退出码 1（CI 性能回归门禁）

默认阈值（宽松，防止大幅退化；可用 --*-p95-ms 按压测结果调整）：
  state.p95 < 1000ms、sidecar_write.p95 < 2000ms、sidecar_exec.p95 < 5000ms
p99 阈值可选（--*-p99-ms），CI 传入后作为 p95 之外的补充回归门禁（v0.35.0）。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import statistics
import sys
import tempfile
import threading
import uuid
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anti_shortcut.proxy_client import GateClient  # noqa: E402
from anti_shortcut.sidecar import GateSidecar, make_handler  # noqa: E402
from anti_shortcut.state import StateManager  # noqa: E402

SPEC = (
    "# 斐波那契函数 Spec\n\n"
    "## 需求分析\n"
    "实现一个函数 fib(n)，返回斐波那契数列第 n 项。约定 F(0)=0, F(1)=1，"
    "n 为自然数，负数输入应抛出 ValueError。\n\n"
    "## 设计方案\n"
    "采用迭代法，滚动维护前两项 a、b，时间复杂度 O(n)，空间复杂度 O(1)，"
    "避免递归导致的指数级开销和栈溢出风险。\n\n"
    "## 接口定义\n"
    "def fib(n: int) -> int — 返回第 n 项；n<0 时抛出 ValueError。\n"
)

TESTS = (
    '"""测试用例"""\n'
    "from fib import fib\n\n"
    "def test_base_cases():\n"
    "    assert fib(0) == 0\n"
    "    assert fib(1) == 1\n\n"
    "def test_known_value():\n"
    "    assert fib(10) == 55\n"
)

IMPL = (
    "def fib(n):\n"
    "    if n < 0:\n"
    "        raise ValueError('n must be >= 0')\n"
    "    if n <= 1:\n"
    "        return n\n"
    "    a, b = 0, 1\n"
    "    for _ in range(n - 1):\n"
    "        a, b = b, a + b\n"
    "    return b\n"
)


def _make_tmp(prefix: str) -> Path:
    """创建可写临时目录：优先系统 Temp，失败则回退到仓库内目录。"""
    try:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        probe = tmp / ".probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return tmp
    except OSError:
        fallback = Path(__file__).resolve().parents[1] / (
            prefix.rstrip("-") + "-" + uuid.uuid4().hex[:8]
        )
        fallback.mkdir(exist_ok=True)
        return fallback


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(p / 100.0 * (len(ordered) - 1))))
    return ordered[idx]


def _summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "p99_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}
    return {
        "count": len(values),
        "p50_ms": round(_percentile(values, 50), 3),
        "p95_ms": round(_percentile(values, 95), 3),
        "p99_ms": round(_percentile(values, 99), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(statistics.mean(values), 3),
    }


def bench_state_contention(threads: int = 100, iters: int = 20) -> dict:
    """多 Agent 共享同一状态文件：并发 ``mark_test_run``（锁 + 重载 + 原子写 + HMAC）。"""
    tmp = _make_tmp("pb-bench-state-")
    try:
        state_file = Path(tmp) / ".agent_gate" / "state.json"
        StateManager(state_file, user_request="性能基准", hmac_key="bench-hmac")
        latencies: list[float] = []
        errors = 0

        def worker(seed: int) -> None:
            nonlocal errors
            mgr = StateManager(state_file, hmac_key="bench-hmac")
            for i in range(iters):
                start = time.perf_counter()
                try:
                    mgr.mark_test_run(
                        {
                            "command": f"pytest worker-{seed}-{i}",
                            "exit_code": 0,
                            "passed": 1,
                            "failed": 0,
                        }
                    )
                    latencies.append((time.perf_counter() - start) * 1000.0)
                except Exception:
                    errors += 1

        with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as pool:
            list(pool.map(worker, range(threads)))
        result = _summary(latencies)
        result["threads"] = threads
        result["iterations_per_thread"] = iters
        result["errors"] = errors
        total_s = sum(latencies) / 1000.0
        result["throughput_ops_per_sec"] = round(len(latencies) / max(1e-9, total_s), 1)
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _seed_sop(gate: GateClient) -> None:
    """铺底 SOP 到阶段 4（实现完成），保证后续写文件与执行命令均被允许。"""
    gate.write_file("spec.md", SPEC)
    gate.advance(2)
    gate.write_file("test_fib.py", TESTS)
    gate.advance(3)
    gate.write_file("fib.py", IMPL)
    gate.advance(4)


def bench_sidecar_write(write_ops: int = 200, workers: int = 16) -> dict:
    """并发经 sidecar 写普通文件（门禁检查 + 落盘）的端到端延迟。"""
    tmp = _make_tmp("pb-bench-write-")
    try:
        ws = Path(tmp) / "ws"
        ws.mkdir(exist_ok=True)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(GateSidecar(ws, user_request="性能基准")),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        gate = GateClient("http://127.0.0.1:%d" % server.server_address[1])
        try:
            _seed_sop(gate)
            per_worker = max(1, write_ops // workers)
            latencies: list[float] = []

            def writer(seed: int) -> None:
                for i in range(per_worker):
                    start = time.perf_counter()
                    gate.write_file(f"notes-{seed}-{i}.txt", "x")
                    latencies.append((time.perf_counter() - start) * 1000.0)

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(writer, range(workers)))
            result = _summary(latencies)
            result["workers"] = workers
            return result
        finally:
            server.shutdown()
            server.server_close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def bench_sidecar_exec(exec_ops: int = 50, workers: int = 8) -> dict:
    """并发经 sidecar 执行 ``echo ok``（门禁检查 + 子进程）的端到端延迟。"""
    tmp = _make_tmp("pb-bench-exec-")
    try:
        ws = Path(tmp) / "ws"
        ws.mkdir(exist_ok=True)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(GateSidecar(ws, user_request="性能基准")),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        gate = GateClient("http://127.0.0.1:%d" % server.server_address[1])
        try:
            _seed_sop(gate)
            per_worker = max(1, exec_ops // workers)
            latencies: list[float] = []

            def executor(seed: int) -> None:
                for i in range(per_worker):
                    start = time.perf_counter()
                    gate.execute_command("echo ok")
                    latencies.append((time.perf_counter() - start) * 1000.0)

            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(executor, range(workers)))
            result = _summary(latencies)
            result["workers"] = workers
            return result
        finally:
            server.shutdown()
            server.server_close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_thresholds(
    results: dict,
    state_p95_ms: float = 1000.0,
    write_p95_ms: float = 2000.0,
    exec_p95_ms: float = 5000.0,
    state_p99_ms: float | None = None,
    write_p99_ms: float | None = None,
    exec_p99_ms: float | None = None,
) -> list[str]:
    """返回超阈值指标列表；为空表示全部通过。p99 阈值可选（v0.35.0）。"""
    failures = []
    state_p95 = results["state"]["p95_ms"]
    if state_p95 > state_p95_ms:
        failures.append(f"state p95 {state_p95}ms > {state_p95_ms}ms")
    write_p95 = results["sidecar_write"]["p95_ms"]
    if write_p95 > write_p95_ms:
        failures.append(f"sidecar_write p95 {write_p95}ms > {write_p95_ms}ms")
    exec_p95 = results["sidecar_exec"]["p95_ms"]
    if exec_p95 > exec_p95_ms:
        failures.append(f"sidecar_exec p95 {exec_p95}ms > {exec_p95_ms}ms")
    state_p99 = results["state"].get("p99_ms", 0.0)
    if state_p99_ms is not None and state_p99 > state_p99_ms:
        failures.append(f"state p99 {state_p99}ms > {state_p99_ms}ms")
    write_p99 = results["sidecar_write"].get("p99_ms", 0.0)
    if write_p99_ms is not None and write_p99 > write_p99_ms:
        failures.append(f"sidecar_write p99 {write_p99}ms > {write_p99_ms}ms")
    exec_p99 = results["sidecar_exec"].get("p99_ms", 0.0)
    if exec_p99_ms is not None and exec_p99 > exec_p99_ms:
        failures.append(f"sidecar_exec p99 {exec_p99}ms > {exec_p99_ms}ms")
    return failures


def _print_table(results: dict) -> None:
    print("\n指标                    count      p50(ms)    p95(ms)    p99(ms)    max(ms)    mean(ms)")
    for name, r in (
        ("state", results["state"]),
        ("sidecar_write", results["sidecar_write"]),
        ("sidecar_exec", results["sidecar_exec"]),
    ):
        print(
            f"{name:<20} {r['count']:>8} {r['p50_ms']:>10.1f} {r['p95_ms']:>10.1f} "
            f"{r['p99_ms']:>10.1f} {r['max_ms']:>10.1f} {r['mean_ms']:>10.1f}"
        )
    st = results["state"]
    print(
        f"\nstate 吞吐: {st['throughput_ops_per_sec']} ops/s "
        f"（{st['threads']} 线程 × {st['iterations_per_thread']} 次/线程，errors={st['errors']}）"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="phase-barrier 性能基准（v0.27.0）")
    parser.add_argument("--json", action="store_true", help="仅输出 JSON")
    parser.add_argument("--fail-fast", action="store_true", help="超阈值时退出码 1")
    parser.add_argument("--state-threads", type=int, default=100)
    parser.add_argument("--state-iters", type=int, default=20)
    parser.add_argument("--write-ops", type=int, default=200)
    parser.add_argument("--exec-ops", type=int, default=50)
    parser.add_argument("--state-p95-ms", type=float, default=1000.0)
    parser.add_argument("--write-p95-ms", type=float, default=2000.0)
    parser.add_argument("--exec-p95-ms", type=float, default=5000.0)
    parser.add_argument("--state-p99-ms", type=float, default=None, help="state p99 阈值（缺省不检查，v0.35.0）")
    parser.add_argument("--write-p99-ms", type=float, default=None, help="sidecar_write p99 阈值（缺省不检查）")
    parser.add_argument("--exec-p99-ms", type=float, default=None, help="sidecar_exec p99 阈值（缺省不检查）")
    parser.add_argument("--output", type=Path, default=None, help="结果另存 JSON 文件")
    args = parser.parse_args(argv)

    print("phase-barrier 性能基准 v0.35.0")
    print("[1/3] StateManager 并发状态写入 ...", flush=True)
    state = bench_state_contention(args.state_threads, args.state_iters)
    print("[2/3] Sidecar 并发 write_file ...", flush=True)
    write = bench_sidecar_write(args.write_ops)
    print("[3/3] Sidecar 并发 execute_command ...", flush=True)
    exec_ = bench_sidecar_exec(args.exec_ops)

    results = {
        "benchmark": "phase-barrier",
        "version": "0.35.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "state": state,
        "sidecar_write": write,
        "sidecar_exec": exec_,
    }

    if args.output:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入: {args.output}")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_table(results)

    if args.fail_fast:
        failures = check_thresholds(
            results,
            state_p95_ms=args.state_p95_ms,
            write_p95_ms=args.write_p95_ms,
            exec_p95_ms=args.exec_p95_ms,
            state_p99_ms=args.state_p99_ms,
            write_p99_ms=args.write_p99_ms,
            exec_p99_ms=args.exec_p99_ms,
        )
        if failures:
            print("性能门禁未通过:", file=sys.stderr)
            for item in failures:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print("性能门禁通过（全部指标在阈值内）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())