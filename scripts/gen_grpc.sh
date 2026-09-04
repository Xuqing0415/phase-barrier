#!/usr/bin/env bash
# 重新生成 anti_shortcut/proto 下 pb2 代码（v0.39.0）。
# 前置：pip install grpcio-tools
# 用法：bash scripts/gen_grpc.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python -m grpc_tools.protoc   -I anti_shortcut/proto   --python_out=anti_shortcut/proto   --grpc_python_out=anti_shortcut/proto   anti_shortcut/proto/sidecar.proto
# 生成器默认顶层导入，改为包内相对导入
python - <<'PY'
import io
p = "anti_shortcut/proto/sidecar_pb2_grpc.py"
s = io.open(p, encoding="utf-8").read()
s = s.replace("import sidecar_pb2 as sidecar__pb2", "from . import sidecar_pb2 as sidecar__pb2", 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("pb2 代码已生成:", p)
PY
