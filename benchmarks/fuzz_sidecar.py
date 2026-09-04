"""sidecar HTTP 与并发文件锁模糊/压力基准（v0.35.0）。

对两类“需要真实环境才能暴露”的边界做确定性模糊测试：

1. ``sidecar_http``：真实起一个 ``GateSidecar``（ThreadingHTTPServer），
   随机发送非法 / 越界 / 半合法 JSON、随机端点与查询串、并发请求，
   断言响应永远是 JSON 错误（400/403/404/409/501）或成功（200），
   服务器进程全程存活（周期性 /healthz 探活）。任何 500、连接断开
   或不可解析响应都计为一次崩溃。
2. ``lock_concurrency``：多进程共享 ``_file_lock``（POSIX flock /
   Windows msvcrt），持锁内原子读改写同一 JSON 计数文件，随机小延迟，
   并让部分 worker 在**持锁状态下异常退出**（os._exit），验证：
   - OS 在进程异常退出时自动释放锁（后续进程不卡死）；
   - 最终计数 == 总操作数 - 崩溃数（无丢失更新 / 重复计数）；
   - 无遗留临时文件，HMAC 签名的 state.json 仍可正常加载。

确定性：固定 --seed 可复现；崩溃率期望 0；--fail-fast 超阈值退出码 1。

用法::

    python benchmarks/fuzz_sidecar.py --http-iterations 200 --lock-rounds 3
    python benchmarks/fuzz_sidecar.py --fail-fast --json
"""
from __future__ import annotations

import argparse
import http.client
import json
import multiprocessing
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anti_shortcut.proxy_client import GateClient  # noqa: E402
from anti_shortcut.sidecar import GateSidecar, make_handler  # noqa: E402
from anti_shortcut.state import StateManager, _file_lock  # noqa: E402

_CRASH_EXIT = 23  # worker 持锁异常退出的哨兵退出码

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

_ENDPOINTS = [
    "/healthz",
    "/api/state",
    "/api/audit",
    "/api/verify-evidence",
    "/api/advance",
    "/api/test-run",
    "/api/source-change",
    "/api/write",
    "/api/exec",
]

# 路径字段的可写 / 可疑候选：避免 NUL 与控制字符（那属于纯解析层 fuzz），
# 聚焦 HTTP 参数校验与门禁路径判定边界。
_WRITE_PATH_POOL = [
    "notes/x.txt",
    "x.py",
    "sub/dir/f.txt",
    "a b c.txt",
    "x'y.txt",
    "中文/测试.txt",
    ".agent_gate/evil.txt",
    "../escape.txt",
    "/tmp/abs.txt",
    "spec.md",
    ".",
    "",
    "a..b",
    "x\ty.txt",
]

_COMMAND_POOL = [
    "echo ok",
    "pwd",
    "printf x\n",
    "true",
    "echo 'quote'",
]

_TEXT_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 "
    "\t\n;&&||><$()'\"\\`*?.!=-_:/[]{}#%+~,"
    "中文测试阶段门禁"
)


def _make_tmp(prefix: str) -> Path:
    try:
        tmp = Path(tempfile.mkdtemp(prefix=prefix))
        (tmp / ".probe").write_text("x", encoding="utf-8")
        (tmp / ".probe").unlink()
        return tmp
    except OSError:
        fallback = Path(__file__).resolve().parents[1] / (
            prefix.rstrip("-") + "-" + uuid.uuid4().hex[:8]
        )
        fallback.mkdir(exist_ok=True)
        return fallback


def _random_text(rng: random.Random, max_len: int = 200) -> str:
    n = rng.randint(0, max_len)
    return "".join(rng.choice(_TEXT_ALPHABET) for _ in range(n))


def _random_json_value(rng: random.Random, depth: int = 0) -> object:
    """随机 JSON 标量 / 浅嵌套结构（含 bool、负数、超大整数、null、数组）。"""
    kind = rng.randint(0, 7)
    if kind <= 2 or depth >= 2:
        return rng.choice(
            [None, True, False, 0, 1, -1, 6, 7, 3601, 1.5, "2", "x", "", "中文"]
        )
    if kind == 3:
        return [rng.choice([None, 1, "a"]) for _ in range(rng.randint(0, 3))]
    if kind == 4:
        return {_random_text(rng, 8): _random_json_value(rng, depth + 1)}
    return {"path": "x.txt", "content": _random_text(rng, 40), "extra": rng.randint(-5, 5)}


def _random_body(rng: random.Random, endpoint: str) -> str | None:
    """按端点生成随机 JSON 载荷（含 ~1/4 概率为不可解析文本 / 非对象 JSON）。"""
    if rng.random() < 0.25:
        return rng.choice(
            [
                _random_text(rng, 60),
                '{"path":',
                "[]",
                "42",
                '"str"',
                "null",
                "{",
                "}{",
                "{\u0000: 1}",
            ]
        )
    if endpoint == "/api/advance":
        value = rng.choice([None, True, False, 0, 1, 6, 7, -1, 1.5, "2", [], {}, rng.randint(-2, 9)])
        return json.dumps({"new_stage": value})
    if endpoint == "/api/test-run":
        exit_code = rng.choice([None, True, False, 0, 1, -1, 256, "0", [], 2])
        output = _random_text(rng, 80) if rng.random() < 0.8 else rng.choice([None, 1, []] )
        return json.dumps({"exit_code": exit_code, "output": output})
    if endpoint == "/api/source-change":
        path = rng.choice([None, 1, [], "", "a.py", "sub/b.py", ".agent_gate/x", "../../x", "a b.py"])
        return json.dumps({"path": path})
    if endpoint == "/api/write":
        path = rng.choice(_WRITE_PATH_POOL) if rng.random() < 0.9 else rng.choice([None, 1, []])
        content = _random_text(rng, 120) if rng.random() < 0.8 else rng.choice([None, 1, []])
        return json.dumps({"path": path, "content": content})
    if endpoint == "/api/exec":
        command = rng.choice(
            [None, "", 1, True, [], _COMMAND_POOL[rng.randrange(len(_COMMAND_POOL))]]
        ) if rng.random() < 0.7 else _random_text(rng, 30)
        timeout = rng.choice([None, 1, 2, 30, 0, -1, 3601, True, 1.5, "5"])
        cwd = rng.choice([None, ".", "sub", 1, "/tmp", "../../", "x y"])
        return json.dumps({"command": command, "timeout": timeout, "cwd": cwd})
    return json.dumps(_random_json_value(rng))


def _random_path(rng: random.Random) -> str:
    path = rng.choice(_ENDPOINTS)
    if rng.random() < 0.35:
        path += rng.choice(
            [
                "/extra",
                "?limit=abc",
                "?limit=0",
                "?limit=999",
                "?limit=5",
                "?offset=-1",
                "?offset=0",
                "?since=garbage",
                "?since=2026-01-01T00:00:00Z",
                "?event=write",
                "?limit=abc&offset=-1",
                "?limit=5&offset=2&since=bad",
                "%2e%2e",
                "//",
            ]
        )
    if rng.random() < 0.2:
        path = "/nope" + rng.choice(["", "/api/write", "?a=b", "/a b", "/.."])
    return path


def _seed_sop(gate: GateClient) -> None:
    """铺底 SOP 到阶段 4（实现完成），保证后续写文件与执行命令均被允许。"""
    gate.write_file("spec.md", SPEC)
    gate.advance(2)
    gate.write_file("test_fib.py", TESTS)
    gate.advance(3)
    gate.write_file("fib.py", IMPL)
    gate.advance(4)


def _request_once(host: str, port: int, method: str, path: str, body: str | None) -> tuple[bool, str]:
    """发送一次随机请求；返回 (是否健康, 说明)。500 / 连接异常 / 响应不可解析 = 不健康。"""
    conn = http.client.HTTPConnection(host, port, timeout=15)
    try:
        payload = None
        headers = {}
        if method == "POST":
            payload = (body or "").encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read()
        if status == 500:
            return False, f"status 500 on {method} {path}"
        if raw:
            try:
                json.loads(raw.decode("utf-8", "replace"))
            except Exception as exc:  # noqa: BLE001
                return False, f"non-json body {type(exc).__name__} on {method} {path}"
        return True, str(status)
    except Exception as exc:  # noqa: BLE001（连接断开 / 请求层异常都视为潜在崩溃）
        return False, f"{type(exc).__name__} on {method} {path}"
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def fuzz_sidecar_http(iterations: int = 300, seed: int = 42) -> int:
    """真实 sidecar HTTP 边界模糊：返回崩溃次数（期望 0）。"""
    rng = random.Random(seed)
    tmp = _make_tmp("pb-fuzz-http-")
    crashes = 0
    try:
        ws = Path(tmp) / "ws"
        ws.mkdir(exist_ok=True)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(GateSidecar(ws, user_request="sidecar 模糊基准")),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[0], server.server_address[1]
        try:
            gate = GateClient("http://127.0.0.1:%d" % port)
            _seed_sop(gate)

            def single() -> bool:
                method = rng.choice(["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
                # 空格等非法请求目标字符做 URL 转义，避免客户端层 InvalidURL 误报；
                # 查询串与 % 编码原样保留，仍可覆盖畸形查询参数。
                path = urllib.parse.quote(_random_path(rng), safe="/?=&%")
                body = _random_body(rng, path) if method == "POST" else None
                ok, detail = _request_once(host, port, method, path, body)
                return ok, detail

            for i in range(iterations):
                ok, detail = single()
                if not ok:
                    crashes += 1
                # 周期性并发突发 + 探活：服务器必须全程存活且响应可解析
                if i % 25 == 0:
                    ok, detail = _request_once(host, port, "GET", "/healthz", None)
                    if not ok:
                        crashes += 1
                if i % 40 == 0 and i > 0:
                    burst = rng.randint(4, 10)
                    with ThreadPoolExecutor(max_workers=burst) as pool:
                        futures = [pool.submit(single) for _ in range(burst)]
                        for fut in futures:
                            ok, detail = fut.result()
                            if not ok:
                                crashes += 1
            return crashes
        finally:
            server.shutdown()
            server.server_close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _lock_worker_main(
    lock_path: str,
    count_path: str,
    ops: int,
    crash_on_last: bool,
    seed: int,
) -> None:
    """锁 fuzz worker：持锁原子递增计数；crash_on_last 时最后一次持锁异常退出。"""
    rng = random.Random(seed)
    lock = Path(lock_path)
    count = Path(count_path)
    for i in range(ops):
        if crash_on_last and i == ops - 1:
            with _file_lock(lock, timeout=120.0):
                os._exit(_CRASH_EXIT)  # noqa: PLR1722（持锁异常退出：验证 OS 自动释放）
        with _file_lock(lock, timeout=120.0):
            if rng.random() < 0.5:
                time.sleep(rng.random() * 0.01)
            value = json.loads(count.read_text(encoding="utf-8"))
            value["count"] = int(value.get("count", 0)) + 1
            tmp = count.with_name(count.name + ".tmp")
            tmp.write_text(json.dumps(value), encoding="utf-8")
            os.replace(tmp, count)


def _lock_round(seed: int, workers: int = 4, ops: int = 5, crash_workers: int = 1) -> None:
    """一轮并发锁压力：期望最终计数 == workers*ops - crash_workers，无临时文件残留。"""
    tmp = _make_tmp("pb-fuzz-lock-")
    try:
        gate_dir = Path(tmp) / ".agent_gate"
        gate_dir.mkdir(parents=True, exist_ok=True)
        state_file = gate_dir / "state.json"
        StateManager(state_file, user_request="锁模糊", hmac_key="fuzz-hmac")
        lock_file = state_file.with_name(state_file.name + ".lock")
        count_file = Path(tmp) / "count.json"
        count_file.write_text(json.dumps({"count": 0}), encoding="utf-8")

        ctx = multiprocessing.get_context("fork" if sys.platform != "win32" else "spawn")
        procs = []
        for w in range(workers):
            proc = ctx.Process(
                target=_lock_worker_main,
                args=(
                    str(lock_file),
                    str(count_file),
                    ops,
                    w < crash_workers,
                    1000 + w + seed,
                ),
            )
            proc.start()
            procs.append(proc)
        crashed = 0
        for proc in procs:
            proc.join(timeout=300)
            if proc.is_alive():
                proc.terminate()
                raise RuntimeError(f"锁 fuzz worker 未在超时内退出: pid={proc.pid}")
            if proc.exitcode == _CRASH_EXIT:
                crashed += 1
            elif proc.exitcode != 0:
                raise RuntimeError(f"锁 fuzz worker 异常退出码 {proc.exitcode}: pid={proc.pid}")

        value = json.loads(count_file.read_text(encoding="utf-8"))
        expected = workers * ops - crash_workers
        if value["count"] != expected:
            raise AssertionError(f"计数不一致: {value['count']} != {expected}（丢更新或重复计数）")
        leftovers = [p.name for p in Path(tmp).glob("*.tmp*")] + [
            p.name for p in gate_dir.glob("*.tmp*")
        ]
        if leftovers:
            raise AssertionError(f"存在遗留临时文件: {leftovers}")
        # HMAC 签名的 state.json 仍可加载（未被并发写坏 / 篡改）
        StateManager(state_file, hmac_key="fuzz-hmac").reload()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fuzz_lock_concurrency(rounds: int = 3, seed: int = 42) -> int:
    """多进程并发锁模糊：返回崩溃 / 断言失败轮数（期望 0）。"""
    crashes = 0
    for r in range(rounds):
        try:
            _lock_round(seed=seed + r * 7)
        except Exception:  # noqa: BLE001
            crashes += 1
    return crashes


def run_fuzz(http_iterations: int = 300, lock_rounds: int = 3, seed: int = 42) -> dict:
    rng = random.Random(seed)
    http_crashes = fuzz_sidecar_http(iterations=http_iterations, seed=rng.randrange(1 << 30))
    lock_crashes = fuzz_lock_concurrency(rounds=lock_rounds, seed=rng.randrange(1 << 30))
    n_cases = http_iterations + lock_rounds
    target_crashes = {"sidecar_http": http_crashes, "lock_concurrency": lock_crashes}
    total = http_crashes + lock_crashes
    return {
        "benchmark": "sidecar-fuzz",
        "version": "0.35.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "http_iterations": http_iterations,
            "lock_rounds": lock_rounds,
            "seed": seed,
        },
        "n_targets": len(target_crashes),
        "n_cases": n_cases,
        "crashes": total,
        "crash_rate": round(total / n_cases if n_cases else 0.0, 6),
        "target_crashes": target_crashes,
    }


def check_thresholds(results: dict, max_crash_rate: float = 0.0) -> list[str]:
    failures = []
    rate = results["crash_rate"]
    if rate > max_crash_rate:
        failures.append(f"崩溃率 {rate:.2%} > 阈值 {max_crash_rate:.0%}")
    for name, crashes in results["target_crashes"].items():
        if crashes:
            failures.append(f"目标 {name} 崩溃 {crashes} 次")
    return failures


def _print_table(results: dict) -> None:
    print("\nsidecar HTTP 与并发锁模糊基准（v0.35.0）")
    print(f"目标数   {results['n_targets']}")
    print(f"用例总数 {results['n_cases']}")
    print(f"崩溃数   {results['crashes']}")
    print(f"崩溃率   {results['crash_rate']:.4%}")
    for name, crashes in results["target_crashes"].items():
        print(f"  {name:<28} {crashes}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="sidecar HTTP 与并发锁模糊基准（v0.35.0）")
    parser.add_argument("--http-iterations", type=int, default=300, help="sidecar HTTP 随机请求数")
    parser.add_argument("--lock-rounds", type=int, default=3, help="多进程并发锁轮数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（确定性复现）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--output", type=Path, default=None, help="写 JSON 到文件")
    parser.add_argument("--fail-fast", action="store_true", help="崩溃率超阈值时退出码 1")
    parser.add_argument("--max-crash-rate", type=float, default=0.0, help="允许的最大崩溃率")
    args = parser.parse_args(argv)

    banner = (
        f"phase-barrier sidecar/锁模糊基准 v0.35.0："
        f"http_iterations={args.http_iterations}, lock_rounds={args.lock_rounds}, "
        f"seed={args.seed}"
    )
    print(banner, file=sys.stderr if args.json else None)
    results = run_fuzz(
        http_iterations=args.http_iterations,
        lock_rounds=args.lock_rounds,
        seed=args.seed,
    )

    if args.output:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已写入: {args.output}")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_table(results)

    if args.fail_fast:
        failures = check_thresholds(results, max_crash_rate=args.max_crash_rate)
        if failures:
            print("模糊测试未通过:", file=sys.stderr)
            for item in failures:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print("模糊测试通过：全部目标 0 崩溃。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
