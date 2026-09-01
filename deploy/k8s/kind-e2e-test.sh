#!/usr/bin/env bash
# phase-barrier Helm chart + kind 端到端测试（v0.27.0）
#
# 验证：helm install 后 sidecar 正常运行；GateClient 经 /api/write、/api/exec
# 跳步被拦截、按 SOP 全通到交付；Agent 容器无法访问门禁状态卷。
#
# 前置：docker、kind、helm、kubectl
# 用法：bash deploy/k8s/kind-e2e-test.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART_DIR="$REPO_ROOT/deploy/helm/phase-barrier"
E2E_VALUES="$REPO_ROOT/deploy/k8s/e2e-values.yaml"
E2E_AGENT="$REPO_ROOT/deploy/k8s/e2e_agent.py"
IMAGE="${E2E_IMAGE:-phase-barrier-demo:e2e}"
CLUSTER_NAME="phase-barrier-e2e"
NAMESPACE="phase-barrier-demo"
RELEASE_NAME="pb"

for tool in kind helm kubectl docker; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "[e2e] 缺少工具: $tool" >&2
    exit 1
  fi
done

cleanup() {
  echo "[e2e] 清理 kind 集群..."
  kind delete cluster --name "$CLUSTER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[e2e] 1/6 构建 e2e 镜像（本地代码 + pytest）"
docker build -f "$REPO_ROOT/deploy/k8s/e2e.Dockerfile" -t "$IMAGE" "$REPO_ROOT"

echo "[e2e] 2/6 创建 kind 集群并加载镜像"
kind create cluster --name "$CLUSTER_NAME" --wait 120s
kind load docker-image "$IMAGE" --name "$CLUSTER_NAME"

echo "[e2e] 3/6 helm lint"
helm lint "$CHART_DIR"

echo "[e2e] 4/6 helm install"
kubectl create namespace "$NAMESPACE" >/dev/null 2>&1 || true
helm install "$RELEASE_NAME" "$CHART_DIR" --namespace "$NAMESPACE" -f "$E2E_VALUES" \
  --set "image.repository=${IMAGE%%:*}" \
  --set "image.tag=${IMAGE##*:}" \
  --set "agent.image.repository=${IMAGE%%:*}" \
  --set "agent.image.tag=${IMAGE##*:}" \
  --wait --timeout 5m

echo "[e2e] 5/6 等待 deployment 就绪"
kubectl rollout status "deployment/${RELEASE_NAME}-phase-barrier" -n "$NAMESPACE" --timeout=180s

POD="$(kubectl get pod -n "$NAMESPACE" \
  -l "app.kubernetes.io/name=phase-barrier,app.kubernetes.io/instance=${RELEASE_NAME}" \
  -o jsonpath='{.items[0].metadata.name}')"
echo "[e2e] pod=${POD}"

echo "[e2e] 6/6 运行 GateClient 端到端 Agent（拦截 + 规范流程）"
kubectl exec -n "$NAMESPACE" "$POD" -c gate-sidecar -i -- python - < "$E2E_AGENT"

echo "[e2e] 验证 Agent 容器无法写入门禁状态卷"
if kubectl exec -n "$NAMESPACE" "$POD" -c agent -- sh -c 'echo tampered > /workspace/.agent_gate/state.json' >/dev/null 2>&1; then
  echo "[e2e] FAIL: agent 竟能写入 .agent_gate（隔离失效）" >&2
  exit 1
fi
echo "[e2e] OK: agent 无法访问门禁状态卷"

echo "[e2e] 全部通过 ✅"