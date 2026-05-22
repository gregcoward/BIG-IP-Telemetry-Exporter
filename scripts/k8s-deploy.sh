#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERLAY="${1:-}"
IMAGE="${IMAGE:-}"

if [[ -z "${OVERLAY}" ]]; then
  if [[ -n "${IMAGE}" ]]; then
    OVERLAY="minimal"
  else
    OVERLAY="local"
    echo "No overlay specified and IMAGE unset — using overlay 'local' (imagePullPolicy: Never)."
    echo "Build and load the image first:"
    echo "  ./scripts/k8s-build-image.sh"
    echo "  ./scripts/k8s-load-image.sh    # kind / minikube / k3d"
    echo ""
  fi
fi

KUSTOMIZE_PATH="${ROOT}/k8s/overlays/${OVERLAY}"

if [[ ! -d "${KUSTOMIZE_PATH}" ]]; then
  echo "Unknown overlay: ${OVERLAY}" >&2
  echo "Available: local, minimal, example" >&2
  exit 1
fi

if [[ "${OVERLAY}" == "minimal" || "${OVERLAY}" == "example" ]]; then
  if [[ -z "${IMAGE}" ]]; then
    echo "ERROR: overlay '${OVERLAY}' expects a registry image." >&2
    echo "" >&2
    echo "The backend image is NOT on Docker Hub. Build, push, then deploy:" >&2
    echo "  ./scripts/k8s-build-image.sh" >&2
    echo "  export IMAGE=ghcr.io/<you>/bigip-metrics-exporter:1.0.0" >&2
    echo "  docker tag bigip-metrics-exporter:latest \"\${IMAGE}\"" >&2
    echo "  docker push \"\${IMAGE}\"" >&2
    echo "  IMAGE=\"\${IMAGE}\" ./scripts/k8s-deploy.sh ${OVERLAY}" >&2
    echo "" >&2
    echo "For a local cluster without a registry:" >&2
    echo "  ./scripts/k8s-build-image.sh && ./scripts/k8s-load-image.sh && ./scripts/k8s-deploy.sh local" >&2
    exit 1
  fi
fi

echo "Applying kustomize overlay: ${OVERLAY}"
kubectl apply -k "${KUSTOMIZE_PATH}"

if [[ -n "${IMAGE}" ]]; then
  echo "Setting deployment image to ${IMAGE}"
  kubectl -n bigip-metrics set image deployment/bigip-metrics-backend "backend=${IMAGE}"
fi

echo ""
echo "Waiting for rollouts..."
kubectl -n bigip-metrics rollout status deployment/otel-collector --timeout=120s
kubectl -n bigip-metrics rollout status deployment/prometheus --timeout=120s
kubectl -n bigip-metrics rollout status deployment/bigip-metrics-backend --timeout=120s
echo ""
echo "Pods:"
kubectl -n bigip-metrics get pods
echo ""
echo "Access UI (port-forward):"
echo "  kubectl -n bigip-metrics port-forward svc/bigip-metrics-backend 8001:8000"
echo "Prometheus:"
echo "  kubectl -n bigip-metrics port-forward svc/prometheus 9090:9090"
