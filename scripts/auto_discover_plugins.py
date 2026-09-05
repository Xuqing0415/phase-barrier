"""第三方插件仓库自动发现（v0.46.0）。

通过 GitHub Search API 按 topic 发现 ``phase-barrier-plugin`` 仓库，过滤已在
``plugins.json`` 中的条目；对新候选执行 ``git clone -> pip install -e ->
anti-shortcut plugin-verify --json`` 全链路验证，通过后以 ``auto_discovered:
true`` 加入索引并同步 ``docs/plugins.md`` 状态表，实现插件索引维护的自动化
（v0.45.0 的 ``verify_plugins.py`` 负责“验证既有条目”，本脚本负责“发现并收录
新条目”，周期 workflow 先发现再全量验证）。

用法::

    python scripts/auto_discover_plugins.py            # 只搜索并打印（dry-run，默认）
    python scripts/auto_discover_plugins.py --dry-run
    python scripts/auto_discover_plugins.py --update   # 验证候选并写回索引 + 同步 docs
    python scripts/auto_discover_plugins.py --update --json

GitHub Search API 认证：``GH_TOKEN`` / ``GITHUB_TOKEN`` 环境变量或 ``--token``
（匿名限流较低，CI 请注入 ``secrets.GITHUB_TOKEN`` / PAT）。退出码：0 = 完成
（含 0 新发现 / disabled）；1 = GitHub API 或本地执行错误（候选验证失败只记录，
不影响退出码，避免网络抖动拖垮周期任务）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = REPO_ROOT / "plugins.json"
DEFAULT_TOPIC = "phase-barrier-plugin"
SEARCH_URL = "https://api.github.com/search/repositories"
USER_AGENT = "phase-barrier-plugin-discovery/0.46"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_plugins  # noqa: E402  仅标准库依赖，复用 load/save/sync-docs


class GithubApiError(RuntimeError):
    """GitHub API 调用失败（含限流 403 / 429）。"""

    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"GitHub API 错误 {status}: {detail[:300]}")
        self.status = status
        self.detail = detail[:300]


def now_iso() -> str:
    """当前 UTC 时间的 ISO-8601 字符串（秒级，Z 后缀），与 verify_plugins 一致。"""
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _token(args_token: str | None = None) -> str | None:
    return args_token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


def github_search(
    topic: str,
    token: str | None = None,
    per_page: int = 50,
    timeout: float = 30.0,
) -> list[dict]:
    """GitHub Search API：返回打了 ``topic`` 的仓库 items（按最近更新排序）。"""
    params = urllib.parse.urlencode(
        {
            "q": f"topic:{topic}",
            "per_page": str(min(max(int(per_page), 1), 100)),
            "sort": "updated",
            "order": "desc",
        }
    )
    req = urllib.request.Request(
        f"{SEARCH_URL}?{params}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")).get("items", [])
    except urllib.error.HTTPError as exc:
        body = ""
        if exc.fp is not None:
            body = exc.read().decode("utf-8", errors="replace")
        raise GithubApiError(exc.code, body) from exc
    except urllib.error.URLError as exc:
        raise GithubApiError(0, f"网络错误: {exc.reason}") from exc


def _normalize_repo(value: str | None) -> str | None:
    """仓库标识归一化（用于去重）：github URL / git URL -> ``owner/repo``（小写）。

    本地相对路径（``./examples/...``）等非 GitHub 目标返回 None，不参与自动去重。
    """
    if not value:
        return None
    s = str(value).strip().rstrip("/")
    if "#" in s:
        s = s.split("#", 1)[0].rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "git+https://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    )
    for prefix in prefixes:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.startswith(("./", "../", ".\\", "..\\")):
        return None
    if s.endswith("/"):
        s = s.rstrip("/")
    parts = s.split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return f"{parts[0].lower()}/{parts[1].lower()}"


def existing_repo_keys(index: list[dict]) -> set[str]:
    """索引中已有条目的归一化 GitHub 标识集合。"""
    keys: set[str] = set()
    for entry in index:
        for raw in (entry.get("repo"), entry.get("install")):
            key = _normalize_repo(raw)
            if key:
                keys.add(key)
    return keys


def _run(cmd: list[str], timeout: float = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def clone_verify(
    repo: dict,
    *,
    python: str | None = None,
    install: bool = True,
) -> tuple[bool, dict, str]:
    """clone -> （可选 pip install -e）-> plugin-verify 验证单个候选仓库。

    返回 ``(ok, entry, reason)``：ok=True 时 entry 为可追加进索引的条目
    （``auto_discovered: true`` + ``last_commit_sha``），reason 为空；ok=False 时
    entry 为空 dict，reason 为失败原因（不含仓库地址，便于摘要拼接）。
    """
    py = python or sys.executable
    clone_url = repo.get("clone_url") or ""
    html_url = repo.get("html_url") or clone_url
    full_name = repo.get("full_name") or ""
    if not clone_url:
        return False, {}, "缺少 clone_url"
    with tempfile.TemporaryDirectory(prefix="pb-discover-", ignore_cleanup_errors=True) as tmp:
        workdir = Path(tmp) / "repo"
        proc = _run(["git", "clone", "--quiet", "--depth", "1", clone_url, str(workdir)], timeout=600)
        if proc.returncode != 0:
            return False, {}, f"git clone 失败: {(proc.stderr or '').strip()[-300:]}"
        sha = ""
        try:
            sha_proc = _run(["git", "-C", str(workdir), "rev-parse", "HEAD"], timeout=60)
            if sha_proc.returncode == 0:
                sha = sha_proc.stdout.strip()[:40]
        except Exception:
            sha = ""
        if install:
            proc = _run([py, "-m", "pip", "install", "-q", "-e", str(workdir)], timeout=600)
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout or "").strip().splitlines()
                detail = "；".join(tail[-3:]) if tail else f"退出码 {proc.returncode}"
                return False, {}, f"pip install 失败: {detail}"
        proc = _run([py, "-m", "anti_shortcut", "plugin-verify", "--json"], timeout=180)
        if proc.returncode != 0:
            return False, {}, f"plugin-verify 失败（rc={proc.returncode}）"
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return False, {}, f"plugin-verify 输出解析失败: {exc}"
        results = payload.get("plugins") or {}
        discovered = payload.get("discovered") or {}
        entry_points: dict[str, list[str]] = {}
        broken: list[str] = []
        total = 0
        for group, items in discovered.items():
            names = [item.get("name") for item in items if item.get("name")]
            if not names:
                continue
            total += len(names)
            ok_names: list[str] = []
            group_results = results.get(group) or {}
            for name in names:
                info = group_results.get(name)
                if info and info.get("ok"):
                    ok_names.append(name)
                else:
                    broken.append(f"{group}:{name}")
            if ok_names:
                entry_points[group] = ok_names
        if total == 0:
            return False, {}, "未发现任何 phase_barrier.* 插件入口点"
        if broken:
            return False, {}, "存在未通过验证的入口点: " + "；".join(broken)
        name = full_name or html_url.rstrip("/").rsplit("/", 1)[-1]
        entry = {
            "name": name,
            "repo": html_url or clone_url,
            "install": f"git+{clone_url}#egg={name.rsplit('/', 1)[-1]}",
            "entry_points": entry_points,
            "last_verified": now_iso(),
            "status": "passed",
            "auto_discovered": True,
            "last_commit_sha": sha or None,
        }
        return True, entry, ""


def discover(
    index_path: Path,
    *,
    topic: str | None = None,
    token: str | None = None,
    dry_run: bool = True,
    per_page: int = 50,
    python: str | None = None,
) -> dict:
    """发现并（可选）收录 topic 仓库：返回结构化摘要。"""
    container = verify_plugins.load_index_file(index_path)
    index = container["plugins"]
    cfg = container.get("auto_discovery") or {}
    topic = topic or cfg.get("github_topic") or DEFAULT_TOPIC
    if not cfg.get("enabled", True):
        return {
            "ran_at": now_iso(),
            "enabled": False,
            "topic": topic,
            "dry_run": dry_run,
            "repos_seen": 0,
            "already_indexed": 0,
            "candidates": 0,
            "candidate_repos": [],
            "added": [],
            "failures": [],
            "index_updated": False,
            "docs_synced": False,
        }
    existing = existing_repo_keys(index)
    items = github_search(topic, token=token, per_page=per_page)
    seen = 0
    already = 0
    candidates: list[dict] = []
    for repo in items:
        if repo.get("fork") or repo.get("archived"):
            continue
        seen += 1
        keys = {
            key
            for key in (
                _normalize_repo(repo.get("html_url")),
                _normalize_repo(repo.get("clone_url")),
            )
            if key
        }
        if keys & existing:
            already += 1
            continue
        candidates.append(repo)
    added: list[dict] = []
    failures: list[dict] = []
    if not dry_run:
        for repo in candidates:
            ok, entry, reason = clone_verify(repo, python=python)
            if ok:
                index.append(entry)
                added.append(entry)
            else:
                failures.append(
                    {
                        "repo": repo.get("html_url") or repo.get("clone_url"),
                        "reason": reason,
                    }
                )
        if added:
            container["plugins"] = index
            Path(index_path).write_text(
                json.dumps(container, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            verify_plugins.sync_index_docs(index)
    return {
        "ran_at": now_iso(),
        "enabled": True,
        "topic": topic,
        "dry_run": dry_run,
        "repos_seen": seen,
        "already_indexed": already,
        "candidates": len(candidates),
        "candidate_repos": [
            repo.get("html_url") or repo.get("full_name") or ""
            for repo in candidates
        ],
        "added": [e["name"] for e in added],
        "failures": failures,
        "index_updated": (not dry_run) and bool(added),
        "docs_synced": (not dry_run) and bool(added),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="第三方 phase-barrier 插件仓库自动发现（v0.46.0）"
    )
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="索引文件路径（默认 plugins.json）")
    parser.add_argument("--topic", default=None, help="GitHub topic（默认取 auto_discovery.github_topic）")
    parser.add_argument("--update", action="store_true", help="验证候选并写回索引 + 同步 docs/plugins.md")
    parser.add_argument("--dry-run", action="store_true", help="只搜索与打印，不修改文件（默认）")
    parser.add_argument("--token", default=None, help="GitHub token（默认读 GH_TOKEN / GITHUB_TOKEN）")
    parser.add_argument("--per-page", type=int, default=50, help="Search API per_page（默认 50，上限 100）")
    parser.add_argument("--json", action="store_true", help="stdout 输出结构化摘要")
    args = parser.parse_args(argv)

    token = _token(args.token)
    index_path = Path(args.index)
    dry_run = not args.update or args.dry_run
    try:
        summary = discover(
            index_path,
            topic=args.topic,
            token=token,
            dry_run=dry_run,
            per_page=args.per_page,
        )
    except GithubApiError as exc:
        print(f"auto-discover 中止：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        if not summary["enabled"]:
            print("auto-discovery 已禁用（plugins.json auto_discovery.enabled=false）")
        elif dry_run:
            print(
                f"[dry-run] topic={summary['topic']}：仓库 {summary['repos_seen']} 个"
                f"（已在索引 {summary['already_indexed']}），候选 {summary['candidates']} 个"
            )
            for repo in summary["candidate_repos"]:
                print(f"  would-add: {repo}")
        else:
            print(
                f"auto-discover 完成：候选 {summary['candidates']}，新增 "
                f"{len(summary['added'])}，失败 {len(summary['failures'])}"
            )
            for entry in summary["added"]:
                print(f"  added: {entry}")
            for fail in summary["failures"]:
                print(f"  skipped: {fail['repo']} -> {fail['reason']}")
            if summary["index_updated"]:
                print("plugins.json 已更新并同步 docs/plugins.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())