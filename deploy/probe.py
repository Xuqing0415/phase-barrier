"""Agent \u4fa7\u63a2\u9488\uff1a\u9a8c\u8bc1 .agent_gate \u53ea\u8bfb\u6302\u8f7d\u751f\u6548\u3001\u5de5\u4f5c\u533a\u53ef\u5199\u3002

\u9884\u671f\u8f93\u51fa\uff1a
  [probe] read state OK: current_stage = 6
  [probe] OK: \u5199\u5165\u95e8\u7981\u76ee\u5f55\u88ab\u62d2\u7edd\uff08PermissionError\uff09
  [probe] OK: \u5de5\u4f5c\u533a\u6e90\u7801\u5199\u5165\u6b63\u5e38
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    ws = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace")
    gate = ws / ".agent_gate"
    state_file = gate / "state.json"

    # 1. \u8bfb\u53d6\u72b6\u6001\uff08\u53ef\u8bfb\uff09
    data = json.loads(state_file.read_text(encoding="utf-8"))
    print(f"[probe] read state OK: current_stage = {data['current_stage']}")

    # 2. \u5c1d\u8bd5\u5199\u5165\u95e8\u7981\u76ee\u5f55\uff08\u53ea\u8bfb\u6302\u8f7d\uff0c\u5e94\u88ab\u62d2\u7edd\uff09
    try:
        (gate / "tamper.txt").write_text("pwned", encoding="utf-8")
        print("[probe] FAIL: \u7adf\u7136\u80fd\u5199\u5165\u95e8\u7981\u76ee\u5f55\uff08\u53ea\u8bfb\u6302\u8f7d\u672a\u751f\u6548\uff09")
        return 1
    except OSError as exc:
        print(f"[probe] OK: \u5199\u5165\u95e8\u7981\u76ee\u5f55\u88ab\u62d2\u7edd\uff08{type(exc).__name__}\uff09")

    # 3. \u5199\u666e\u901a\u6e90\u7801\uff08\u5e94\u6210\u529f\uff09
    (ws / "agent_hello.py").write_text("print('hello from agent')\n", encoding="utf-8")
    print("[probe] OK: \u5de5\u4f5c\u533a\u6e90\u7801\u5199\u5165\u6b63\u5e38")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
