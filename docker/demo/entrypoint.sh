#!/usr/bin/env bash
# phase-barrier 一键体验入口：自动运行模拟 Agent，展示门禁拦截与规范流程。
set -euo pipefail

echo "============================================================"
echo " phase-barrier 一键体验（stage gate demo）"
echo "============================================================"
python /opt/phase-barrier-demo/agent_demo.py
