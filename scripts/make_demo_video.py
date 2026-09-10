#!/usr/bin/env python3
"""生成 phase-barrier 演示视频（B2，2.5 分钟，1280x720 mp4）。

设计要点：

- **不伪造输出**：终端画面来自 `docker/demo/agent_demo.py` 的真实运行结果
  （本脚本现场执行该脚本并捕获 stdout），旁白/说明由 drawtext 叠加。
- **可复现**：同一份输入生成同一份视频，无需 OBS 录屏；`ffmpeg` 需在 PATH 中。
- **字体**：优先用 `PB_VIDEO_FONT_MONO` / `PB_VIDEO_FONT_CJK` 指定，否则按平台
  常见路径探测（Windows: consola.ttf / msyh.ttc；Linux: DejaVuSansMono / NotoSansCJK；
  macOS: Menlo / PingFang）。字体文件会被复制到工作目录，避免 Windows 盘符冒号
  在 ffmpeg filtergraph 里需要转义。

用法：

    python scripts/make_demo_video.py --out docs/media/phase-barrier-demo.mp4
    python scripts/make_demo_video.py --smoke          # 20 秒快速校验布局

退出码：0 成功；1 缺少 ffmpeg / 字体；2 演示脚本运行失败。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

WIDTH, HEIGHT, FPS = 1280, 720, 30

BG, FG, MUTED = "0x0D1117", "0xE6EDF3", "0x8B949E"
GREEN, RED, BLUE, YELLOW = "0x7EE787", "0xFF7B72", "0x79C0FF", "0xFFD866"
PANEL, BORDER = "0x161B22", "0x30363D"

#: 场景时间窗（秒）。相邻场景首尾相接且不重叠，由 validate_timeline() 守护。
SCENES: tuple[tuple[str, float, float], ...] = (
    ("title", 0.0, 15.5),
    ("terminal", 15.5, 46.0),
    ("defense", 46.0, 72.0),
    ("features", 72.0, 96.0),
    ("quickstart", 96.0, 122.0),
    ("cta", 122.0, 150.0),
)

#: 视频总时长（秒）。
DURATION = SCENES[-1][2]

_CJK_RANGES = ((0x2E80, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF), (0x3000, 0x303F))


def has_cjk(text: str) -> bool:
    """文本是否含中日韩字符（决定用等宽字体还是 CJK 字体）。"""
    return any(any(lo <= ord(ch) <= hi for lo, hi in _CJK_RANGES) for ch in text)


def scene(name: str) -> tuple[float, float]:
    for sname, start, end in SCENES:
        if sname == name:
            return start, end
    raise KeyError(name)


def validate_timeline(scenes=SCENES) -> None:
    """时间窗必须递增且互不重叠，否则场景文字会互相叠字（曾实际发生）。"""
    prev_end = None
    prev_name = None
    for name, start, end in scenes:
        if end <= start:
            raise ValueError(f"场景 {name} 的时间窗非法：{start} -> {end}")
        if prev_end is not None and start < prev_end:
            raise ValueError(f"场景 {name} 与 {prev_name} 重叠：{start} < {prev_end}")
        prev_end, prev_name = end, name


def drawtext_filter(textfile: str, font: str, size: int, color: str, x, y: int,
                    enable: str, center: bool = False) -> str:
    """拼一条 drawtext 滤镜。

    `expansion=none` 必须保留：否则文本里的 `%`（如 "25%"）会被 ffmpeg 当作
    表达式展开，输出 "Stray %" 警告并吞掉内容。
    """
    x_expr = "(w-text_w)/2" if center else str(x)
    return (
        f"drawtext=fontfile={font}:textfile={textfile}"
        f":fontcolor={color}:fontsize={size}:x={x_expr}:y={y}"
        f":expansion=none:enable='{enable}'"
    )


def find_font(env_var: str, candidates: tuple[str, ...]) -> Path | None:
    override = os.environ.get(env_var)
    if override:
        p = Path(override)
        return p if p.is_file() else None
    for cand in candidates:
        p = Path(cand)
        if p.is_file():
            return p
    return None


def fonts() -> tuple[Path, Path]:
    mono = find_font("PB_VIDEO_FONT_MONO", (
        r"C:\Windows\Fonts\consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
    ))
    cjk = find_font("PB_VIDEO_FONT_CJK", (
        r"C:\Windows\Fonts\msyh.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ))
    if mono is None or cjk is None:
        raise FileNotFoundError(
            "缺少字体：请设置 PB_VIDEO_FONT_MONO / PB_VIDEO_FONT_CJK 指向 .ttf/.ttc 文件"
        )
    return mono, cjk


def capture_demo(workdir: Path, timeout: int = 300) -> list[str]:
    """现场运行 docker/demo/agent_demo.py，返回其真实 stdout 行。"""
    ws = workdir / "demo_ws"
    ws.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "PB_DEMO_WS": str(ws),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    })
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "docker" / "demo" / "agent_demo.py")],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stdout or "") + (proc.stderr or ""))
    lines = (proc.stdout or "").splitlines()
    for i, line in enumerate(lines):
        if "[\u9700\u6c42]" in line:          # [需求] 起才是有效内容
            return lines[i:]
    return lines


def timeline_entries(demo_lines: list[str]) -> list[dict]:
    """把真实输出与旁白编译成 drawtext 条目列表（纯函数，便于测试）。"""
    term0, term1 = scene("terminal")
    l5a, l5b = scene("defense")
    more_a, more_b = scene("features")
    qs_a, qs_b = scene("quickstart")
    cta_a, _ = scene("cta")

    out: list[dict] = []

    def add(text, x, y, size=19, color=FG, enable="", center=False):
        out.append(dict(text=text, x=x, y=y, size=size, color=color,
                        enable=enable, center=center))

    # 场景 1：标题
    add("phase-barrier", 0, 232, size=76, color=GREEN, enable="between(t,0.4,15.5)", center=True)
    add("让编码 Agent 遵循工程师 SOP，一步都不能跳", 0, 342, size=31, color=FG,
        enable="between(t,1.6,15.5)", center=True)
    add("需求 → Spec → 测试 → 实现 → 测试 → 交付", 0, 404, size=24, color=MUTED,
        enable="between(t,3.0,15.5)", center=True)
    add("Agent 会跳过设计、跳过测试，直接写实现。", 0, 286, size=33, color=FG,
        enable="between(t,9.0,15.5)", center=True)
    add("更麻烦的是：它会把需求里的约束悄悄改松。", 0, 344, size=33, color=RED,
        enable="between(t,11.0,15.5)", center=True)

    # 场景 2：终端回放（逐行出现）
    step = (term1 - term0 - 2.0) / max(1, len(demo_lines))
    for i, line in enumerate(demo_lines):
        if not line.strip():
            continue
        start = term0 + 0.8 + i * step
        if "[拦截]" in line:
            color = RED
        elif "advance_stage" in line:
            color = GREEN
        elif line.startswith("["):
            color = BLUE
        else:
            color = FG
        size = 19 if len(line) < 58 else 17
        add(("  " + line) if not line.startswith(" ") else line, 68, 112 + i * 22,
            size=size, color=color, enable=f"between(t,{start:.2f},{term1})")
    add("真实运行：docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo", 68, 592,
        size=18, color=MUTED, enable=f"between(t,{term0},{term1})")

    def bullets(lines, start, end, *, per, y0, size, gap, bars):
        for i, line in enumerate(lines):
            st = start + i * per
            add(line, 90, y0 + i * gap, size=size, enable=f"between(t,{st},{end})")
            if line:
                add("|", 62, y0 + i * gap, size=size, color=bars[i % len(bars)],
                    enable=f"between(t,{st},{end})")

    bullets(["五道防线：拦截「隐性约束篡改」",
             "① 需求模板 —— 目标 / 禁止行为 / 接口 / 验收标准强制结构化",
             "② 双模型交叉复核 —— 覆盖核查 + 篡改核查，任一失败即拒绝",
             "③ 形式化校验 —— 约束 DSL → TLA+ / TLC，抓逻辑自相矛盾",
             "④ 运行时行为审计 —— 工具调用 trace 对比 spec 声明的行为",
             "⑤ 概率人工复核 —— 按风险打分抽样，兜住自动化漏洞"],
            l5a, l5b, per=1.5, y0=190, size=27, gap=62,
            bars=[GREEN, BLUE, BLUE, YELLOW, RED, MUTED])

    bullets(["13 种语言适配器",
             "Python / JS·TS / Java / Kotlin / Scala / Go / Rust",
             "Swift / Ruby / PHP / C#（.NET）/ C++ / Dart",
             "SWE-bench 双组实测公开可复算",
             "scale-20：baseline 25% vs gated 25%，19 次拦截",
             "插件生态：打 topic 即被每周自动收录（增量刷新已实证）"],
            more_a, more_b, per=1.5, y0=196, size=26, gap=62,
            bars=[GREEN, MUTED, MUTED, GREEN, MUTED, GREEN])

    bullets(["两种上手方式", "",
             "$ docker run --rm -it ghcr.io/xuqing0415/phase-barrier-demo", "",
             "$ pip install phase-barrier", "$ python -m anti_shortcut init"],
            qs_a, qs_b, per=1.5, y0=196, size=27, gap=58,
            bars=[GREEN, MUTED, FG, MUTED, FG, FG])

    add("GitHub  Xuqing0415/phase-barrier", 0, 280, size=36, color=FG,
        enable=f"between(t,{cta_a},1e6)", center=True)
    add("文档站  docs.xshayncka.dev", 0, 336, size=30, color=BLUE,
        enable=f"between(t,{cta_a + 1.5},1e6)", center=True)
    add("给个 Star，或打上 topic 成为下一个被收录的插件", 0, 402, size=27, color=GREEN,
        enable=f"between(t,{cta_a + 3.0},1e6)", center=True)

    add("phase-barrier · 阶段门禁反捷径校验框架", 40, 686, size=17, color="0x6E7681",
        enable="between(t,3.0,1e6)")
    return out


def build_filtergraph(entries, fonts_map, workdir: Path) -> str:
    """生成 filtergraph 文本，并把每段文字写成独立 textfile（免转义）。"""
    parts = []
    term0, term1 = scene("terminal")
    parts += [
        f"drawbox=x=40:y=64:w=1200:h=560:color={PANEL}:t=fill:enable='between(t,{term0},{term1})'",
        f"drawbox=x=40:y=64:w=1200:h=560:color={BORDER}:t=2:enable='between(t,{term0},{term1})'",
        f"drawbox=x=62:y=84:w=14:h=14:color=0xFF5F56:t=fill:enable='between(t,{term0},{term1})'",
        f"drawbox=x=86:y=84:w=14:h=14:color=0xFFBD2E:t=fill:enable='between(t,{term0},{term1})'",
        f"drawbox=x=110:y=84:w=14:h=14:color=0x27C93F:t=fill:enable='between(t,{term0},{term1})'",
    ]
    for idx, item in enumerate(entries):
        name = f"line_{idx:03d}.txt"
        (workdir / name).write_text(item["text"], encoding="utf-8", newline="")
        font = fonts_map["cjk"] if has_cjk(item["text"]) else fonts_map["mono"]
        parts.append(drawtext_filter(name, font, item["size"], item["color"],
                                     item["x"], item["y"], item["enable"],
                                     center=item["center"]))
    return ",".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 phase-barrier 演示视频")
    ap.add_argument("--out", default="phase-barrier-demo.mp4")
    ap.add_argument("--duration", type=float, default=DURATION)
    ap.add_argument("--smoke", action="store_true", help="只渲染 20 秒用于校验布局")
    ap.add_argument("--keep-workdir", action="store_true")
    ap.add_argument("--workdir", default=None,
                    help="工作目录（默认用系统临时目录；沙箱/受限 ACL 环境可显式指定）")
    args = ap.parse_args(argv)

    if shutil.which("ffmpeg") is None:
        print("需要 ffmpeg（未在 PATH 中找到）", file=sys.stderr)
        return 1
    try:
        mono, cjk = fonts()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    validate_timeline()

    if args.workdir:
        workdir = Path(args.workdir)
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix="pb-video-"))
    try:
        mono_name = "mono" + mono.suffix
        cjk_name = "cjk" + cjk.suffix
        shutil.copyfile(mono, workdir / mono_name)
        shutil.copyfile(cjk, workdir / cjk_name)
        fonts_map = {"mono": mono_name, "cjk": cjk_name}

        try:
            demo_lines = capture_demo(workdir)
        except Exception as exc:  # noqa: BLE001 - 透传演示脚本失败原因
            print(f"演示脚本运行失败：{exc}", file=sys.stderr)
            return 2

        entries = timeline_entries(demo_lines)
        (workdir / "filters.txt").write_text(
            build_filtergraph(entries, fonts_map, workdir), encoding="utf-8", newline="")

        duration = 20.0 if args.smoke else args.duration
        dst = Path(args.out)
        if not dst.is_absolute():
            dst = Path.cwd() / dst
        dst.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "warning", "-y",
            "-f", "lavfi", "-i", f"color=c={BG}:s={WIDTH}x{HEIGHT}:r={FPS}:d={duration}",
            "-/filter", "filters.txt",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(dst),
        ]
        proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.stderr.strip():
            print(proc.stderr.strip()[-2000:], file=sys.stderr)
        if proc.returncode != 0:
            return 1
        print(f"已生成 {dst}（{dst.stat().st_size / 1e6:.2f} MB，{duration:.0f}s）")
        return 0
    finally:
        if not args.keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
