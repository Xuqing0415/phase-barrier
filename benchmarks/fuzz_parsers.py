"""解析器模糊测试基准（v0.31.0）：对输出解析 / 路径提取 / 文件识别等纯函数做确定性模糊测试。

设计：
- 用固定种子（--seed）驱动伪随机输入生成，CI 可复现；
- 每个目标函数跑 --iterations 次随机输入，捕获异常计为 crash；
- 期望 crash 率为 0；--fail-fast 时超过 --max-crash-rate 即退出码 1。

用法::

    python benchmarks/fuzz_parsers.py                    # 12 目标 x 1000 次
    python benchmarks/fuzz_parsers.py --iterations 3000 --json
    python benchmarks/fuzz_parsers.py --fail-fast --max-crash-rate 0
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anti_shortcut.config import GateConfig  # noqa: E402
from anti_shortcut.interceptors import (  # noqa: E402
    _extract_coverage,
    extract_written_paths,
    is_language_test_command,
    summarize_test_output,
    touches_gate_dir,
)
from anti_shortcut.languages import LANGUAGE_REGISTRY  # noqa: E402
from anti_shortcut.languages.java import (  # noqa: E402
    JavaAdapter,
    _extract_build_errors,
    _extract_java_failures,
    _extract_java_failures_detailed,
    _extract_javac_errors,
    _gradle_aggregate,
    _gradle_summary,
)
from anti_shortcut.languages.csharp import (  # noqa: E402
    CSharpAdapter,
    _extract_build_errors as _csharp_extract_build_errors,
)
from anti_shortcut.languages.scala import ScalaAdapter  # noqa: E402
from anti_shortcut.languages.swift import SwiftAdapter  # noqa: E402
from anti_shortcut.languages.dart import DartAdapter  # noqa: E402

_TEXT_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \t\n"
    ";&&||><$()'\"\\`*?.!=-_:/[]{}#%+~,"
    "\x1b[31m\x1b[0m\x1b]0;title\x07"
    "\x00"
    "中文测试接口阶段门禁"
)

_PATH_POOL = [
    "test_x.py",
    "test_fib.py",
    "src/app.js",
    "app.ts",
    "TestFoo.java",
    "foo_test.go",
    "main.rs",
    "test.php",
    "a b c.py",
    "x'y.py",
    "a..b",
    "../escape",
    "C:\\Temp\\x.py",
    "spec.md",
    ".agent_gate/state.json",
    "tests/test_a.py",
    "__tests__/b.test.js",
    "Makefile",
    "",
]


def _random_text(rng: random.Random, max_len: int = 400) -> str:
    """生成随机文本：ASCII / shell 特殊字符 / ANSI 转义 / NUL / 中文。"""
    n = rng.randint(0, max_len)
    return "".join(rng.choice(_TEXT_ALPHABET) for _ in range(n))


def _random_exit_code(rng: random.Random) -> int | None:
    return rng.choice([0, 1, 2, 5, -1, None, rng.randint(-10, 10)])


def _random_path(rng: random.Random) -> str:
    n = rng.randint(0, 5)
    if n == 0:
        return rng.choice(_PATH_POOL)
    return "/".join(rng.choice(_PATH_POOL) for _ in range(n))


def fuzz_summarize_test_output(rng: random.Random, n: int) -> int:
    crashes = 0
    for _ in range(n):
        try:
            r = summarize_test_output(_random_text(rng), _random_exit_code(rng))
            for key in ("passed", "summary", "exit_code", "coverage", "output_tail"):
                assert key in r
            assert isinstance(r["passed"], bool)
            assert isinstance(r["summary"], str)
        except Exception:
            crashes += 1
    return crashes


def fuzz_extract_coverage(rng: random.Random, n: int) -> int:
    crashes = 0
    for _ in range(n):
        try:
            v = _extract_coverage(_random_text(rng))
            assert v is None or isinstance(v, float)
        except Exception:
            crashes += 1
    return crashes


def fuzz_extract_written_paths(rng: random.Random, n: int) -> int:
    crashes = 0
    for _ in range(n):
        try:
            paths = extract_written_paths(_random_text(rng))
            assert isinstance(paths, list) and all(isinstance(p, str) for p in paths)
        except Exception:
            crashes += 1
    return crashes


def fuzz_touches_gate_dir(rng: random.Random, n: int) -> int:
    crashes = 0
    gate_names = [".agent_gate", "/tmp/.agent_gate", "gate", "state"]
    for _ in range(n):
        try:
            v = touches_gate_dir(_random_text(rng), Path(rng.choice(gate_names)))
            assert isinstance(v, bool)
        except Exception:
            crashes += 1
    return crashes


def fuzz_java_module_parsers(rng: random.Random, n: int) -> int:
    crashes = 0
    for _ in range(n):
        text = _random_text(rng)
        try:
            agg = _gradle_aggregate(text)
            assert agg is None or (isinstance(agg, tuple) and len(agg) == 3)
            if agg is not None:
                assert isinstance(_gradle_summary(agg), str)
            det = _extract_java_failures_detailed(text)
            assert isinstance(det, list)
            assert all(isinstance(t, tuple) and len(t) == 2 for t in det)
            assert isinstance(_extract_java_failures(text), list)
            assert isinstance(_extract_javac_errors(text), list)
            assert isinstance(_extract_build_errors(text), list)
        except Exception:
            crashes += 1
    return crashes


def fuzz_java_parse_output(rng: random.Random, n: int) -> int:
    crashes = 0
    adapter = JavaAdapter()
    for _ in range(n):
        try:
            ok, summary = adapter.parse_test_output(_random_text(rng), _random_exit_code(rng))
            assert isinstance(ok, bool)
            assert isinstance(summary, str)
        except Exception:
            crashes += 1
    return crashes


def fuzz_csharp_parsers(rng: random.Random, n: int) -> int:
    crashes = 0
    adapter = CSharpAdapter()
    for _ in range(n):
        text = _random_text(rng)
        try:
            assert isinstance(_csharp_extract_build_errors(text), list)
            ok, summary = adapter.parse_test_output(text, _random_exit_code(rng))
            assert isinstance(ok, bool)
            assert isinstance(summary, str)
        except Exception:
            crashes += 1
    return crashes


def fuzz_scala_parsers(rng: random.Random, n: int) -> int:
    crashes = 0
    adapter = ScalaAdapter()
    for _ in range(n):
        text = _random_text(rng)
        try:
            ok, summary = adapter.parse_test_output(text, _random_exit_code(rng))
            assert isinstance(ok, bool)
            assert isinstance(summary, str)
        except Exception:
            crashes += 1
    return crashes


def fuzz_swift_parsers(rng: random.Random, n: int) -> int:
    crashes = 0
    adapter = SwiftAdapter()
    for _ in range(n):
        try:
            ok, summary = adapter.parse_test_output(_random_text(rng), _random_exit_code(rng))
            assert isinstance(ok, bool)
            assert isinstance(summary, str)
        except Exception:
            crashes += 1
    return crashes


def fuzz_dart_parsers(rng: random.Random, n: int) -> int:
    crashes = 0
    adapter = DartAdapter()
    for _ in range(n):
        try:
            ok, summary = adapter.parse_test_output(_random_text(rng), _random_exit_code(rng))
            assert isinstance(ok, bool)
            assert isinstance(summary, str)
        except Exception:
            crashes += 1
    return crashes


def fuzz_adapters_classify(rng: random.Random, n: int) -> int:
    crashes = 0
    adapters = [cls() for cls in LANGUAGE_REGISTRY.values()]
    for _ in range(n):
        path = _random_path(rng)
        for ad in adapters:
            try:
                assert isinstance(ad.is_test_file(path), bool)
                assert isinstance(ad.is_source_file(path), bool)
            except Exception:
                crashes += 1
    return crashes


def fuzz_is_test_command(rng: random.Random, n: int) -> int:
    crashes = 0
    cfg = GateConfig()
    for _ in range(n):
        try:
            assert isinstance(is_language_test_command(_random_text(rng), cfg), bool)
        except Exception:
            crashes += 1
    return crashes


TARGETS: dict[str, object] = {
    "summarize_test_output": fuzz_summarize_test_output,
    "extract_coverage": fuzz_extract_coverage,
    "extract_written_paths": fuzz_extract_written_paths,
    "touches_gate_dir": fuzz_touches_gate_dir,
    "java_module_parsers": fuzz_java_module_parsers,
    "java_parse_output": fuzz_java_parse_output,
    "adapters_classify": fuzz_adapters_classify,
    "is_test_command": fuzz_is_test_command,
    "csharp_parsers": fuzz_csharp_parsers,
    "scala_parsers": fuzz_scala_parsers,
    "swift_parsers": fuzz_swift_parsers,
    "dart_parsers": fuzz_dart_parsers,
}


def run_fuzz(iterations: int = 1000, seed: int = 42) -> dict:
    rng = random.Random(seed)
    target_crashes: dict[str, int] = {}
    total_cases = 0
    total_crashes = 0
    for name, fn in TARGETS.items():
        crashes = fn(rng, iterations)
        target_crashes[name] = crashes
        total_cases += iterations
        total_crashes += crashes
    n_cases = total_cases
    return {
        "benchmark": "parser-fuzz",
        "version": "0.31.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "iterations_per_target": iterations,
            "seed": seed,
            "targets": sorted(TARGETS),
        },
        "n_targets": len(TARGETS),
        "n_cases": n_cases,
        "crashes": total_crashes,
        "crash_rate": round(total_crashes / n_cases if n_cases else 0.0, 6),
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
    print("\n解析器模糊测试基准（v0.31.0）")
    print(f"目标数   {results['n_targets']}")
    print(f"用例总数 {results['n_cases']}")
    print(f"崩溃数   {results['crashes']}")
    print(f"崩溃率   {results['crash_rate']:.4%}")
    for name, crashes in results["target_crashes"].items():
        print(f"  {name:<28} {crashes}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="解析器模糊测试基准（v0.31.0）")
    parser.add_argument("--iterations", type=int, default=1000, help="每个目标的随机输入数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（确定性复现）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--output", type=Path, default=None, help="写 JSON 到文件")
    parser.add_argument("--fail-fast", action="store_true", help="崩溃率超阈值时退出码 1")
    parser.add_argument("--max-crash-rate", type=float, default=0.0, help="允许的最大崩溃率")
    args = parser.parse_args(argv)

    banner = (
        f"phase-barrier 解析器模糊测试基准 v0.31.0："
        f"iterations={args.iterations}, seed={args.seed}"
    )
    print(banner, file=sys.stderr if args.json else None)
    results = run_fuzz(iterations=args.iterations, seed=args.seed)

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
