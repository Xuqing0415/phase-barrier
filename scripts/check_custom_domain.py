"""docs.phase-barrier.dev 自定义域名检查（v0.48.0，纯标准库）。

检查 ``docs/CNAME`` 是否存在且内容与期望域名一致（MkDocs 会把该文件
复制到站点根目录，GitHub Pages 据此应用自定义域名）。

- 默认非阻塞：未配置 / 配置不一致时打印警告并返回 0（供 CI 使用，
  不影响部署）。
- ``--strict``：未配置 / 配置不一致时返回 1（供本地验收 / 发布前检查）。

用法::

    python scripts/check_custom_domain.py
    python scripts/check_custom_domain.py --strict
    python scripts/check_custom_domain.py --cname docs/CNAME --domain docs.phase-barrier.dev
"""
from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CNAME = REPO_ROOT / "docs" / "CNAME"
DEFAULT_DOMAIN = "docs.phase-barrier.dev"


def check_cname(cname: Path, domain: str) -> tuple[bool, str]:
    """返回 ``(configured_ok, message)``：cname 缺失或内容不一致时 ok=False。"""
    if not cname.is_file():
        return False, (
            f"{cname} 不存在：自定义域名 {domain} 未启用（可选）。"
            "启用方法见 docs/custom-domain.md；未启用不影响功能。"
        )
    raw = cname.read_text(encoding="utf-8").strip()
    if raw != domain:
        return False, (
            f"{cname} 内容为 {raw!r}，与期望域名 {domain!r} 不一致。"
            "如需切换域名请同时更新 DNS 记录与 GitHub Pages 设置。"
        )
    return True, f"自定义域名 {domain} 已配置（{cname}），检查通过"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查 GitHub Pages 自定义域名 CNAME（docs.phase-barrier.dev，v0.48.0）"
    )
    parser.add_argument("--cname", default=str(DEFAULT_CNAME), help="CNAME 文件路径（默认 docs/CNAME）")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN, help="期望域名（默认 docs.phase-barrier.dev）")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="严格模式：未配置 / 配置不一致时返回非零（默认仅警告，返回 0）",
    )
    args = parser.parse_args(argv)

    ok, message = check_cname(Path(args.cname), args.domain)
    if ok:
        print(f"[check-custom-domain] OK：{message}")
        return 0
    if args.strict:
        print(f"[check-custom-domain] FAIL：{message}")
        return 1
    print(f"[check-custom-domain] WARN：{message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())