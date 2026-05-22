# Kubernetes installation guide

This document is the detailed companion to the [Kubernetes section in README](../README.md). It describes how to run the **entire** BIG-IP Metrics Exporter stack on Kubernetes:

- **bigip-metrics-backend** — Python API + React UI (single container image)
- **otel-collector** — OpenTelemetry Collector Contrib
- **prometheus** — scrapes the collector’s Prometheus exporter for validation

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| Kubernetes 1.25+ | Any common distribution (EKS, GKE, AKS, OpenShift, k3s, etc.) |
| `kubectl` | Configured for your cluster |
| Container runtime | To build/push the backend image (`docker` or `podman`) |
| Network path to BIG-IP | Pods must reach the BIG-IP management IP (typically HTTPS :443) |
| Optional Ingress | Only if you use the `base` or `example` overlay with Ingress enabled |

## Manifest layout

```
k8s/
├── base/                          # Full stack (includes sample Ingress)
│   ├── namespace.yaml
│   ├── configmap-otel-collector.yaml
│   ├── configmap-prometheus.yaml
│   ├── rbac-prometheus.yaml
│   ├── deployment-*.yaml
│   ├── service-*.yaml
│   ├── ingress.yaml
│   └── kustomization.yaml
└── overlays/
    ├── minimal/                   # Same stack, no Ingress
    └── example/                   # Sample registry + hostnames
```

## Install steps

### 1. Build and publish the backend image

The backend image bundles the React UI (`frontend/dist`) and Python API.

```bash
chmod +x scripts/k8s-build-image.sh
./scripts/k8s-build-image.sh

# Tag and push to your registry, for example:
docker tag bigip-metrics-exporter:latest ghcr.io/<org>/bigip-metrics-exporter:1.0.0
docker push ghcr.io/<org>/bigip-metrics-exporter:1.0.0
```

### 2. Choose an overlay

| Overlay | Use when |
|---------|----------|
| **`local`** | You built the image locally and loaded it into kind/minikube/k3d (or Docker Desktop) |
| `minimal` | Image is in a **registry**; set `IMAGE=...` when running `k8s-deploy.sh` |
| `example` | Registry + Ingress hostnames (edit `kustomization.yaml`) |
| `base` | Raw manifests; same image requirements as `minimal` |

> **Important:** `bigip-metrics-exporter:latest` is **not** on Docker Hub. An unqualified name resolves to `docker.io/library/bigip-metrics-exporter`, which causes `ErrImagePull` / `authorization failed`.

### 3. Deploy

**Local image (recommended for dev):**

```bash
chmod +x scripts/k8s-build-image.sh scripts/k8s-load-image.sh scripts/k8s-deploy.sh
./scripts/k8s-build-image.sh
./scripts/k8s-load-image.sh
./scripts/k8s-deploy.sh local
```

**Registry:**

```bash
export IMAGE=ghcr.io/<org>/bigip-metrics-exporter:1.0.0
docker tag bigip-metrics-exporter:latest "${IMAGE}"
docker push "${IMAGE}"
IMAGE="${IMAGE}" ./scripts/k8s-deploy.sh minimal
```

### 4. Access the UI and Prometheus

**Port-forward (minimal overlay):**

```bash
kubectl -n bigip-metrics port-forward svc/bigip-metrics-backend 8001:8000
kubectl -n bigip-metrics port-forward svc/prometheus 9090:9090
```

- UI + API: http://127.0.0.1:8001 (local port **8001** → service port 8000)  
- Prometheus: http://127.0.0.1:9090  

**Ingress:** Update hosts in `k8s/base/ingress.yaml` or the `example` overlay patch, set `ingressClassName` for your controller, then apply an overlay that includes Ingress.

### 5. Configure collector exporters from the UI

1. Open the UI → **OpenTelemetry Collector exporters** → **Apply collector config**.
2. Sync YAML into the cluster ConfigMap and restart the collector:

```bash
# With backend port-forward running on :8001
chmod +x scripts/k8s-apply-collector-config.sh
./scripts/k8s-apply-collector-config.sh
```

Or manually:

```bash
kubectl -n bigip-metrics create configmap otel-collector-config \
  --from-file=config.yaml=./otel-collector/generated-config.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n bigip-metrics rollout restart deployment/otel-collector
```

### 6. Connect to BIG-IP and start export

Credentials are **not** stored in Kubernetes Secrets by default—they are entered in the UI per session (same as local/docker). Ensure:

- The management IP is reachable from pods.
- TLS verification matches your BIG-IP cert (checkbox in UI).

The backend uses `OTLP_HTTP_ENDPOINT=http://otel-collector.bigip-metrics.svc.cluster.local:4318` (set in the Deployment).

### 7. Validate metrics in Prometheus

In Prometheus, run a query such as:

```promql
{bigip_endpoint=~".+"}
```

Or prefix search: `bigip_tm_ltm_virtual_stats`

Confirm the `otel-collector` scrape target is **UP** (Status → Targets).

## Environment variables (backend)

| Variable | Default (K8s manifest) | Purpose |
|----------|------------------------|---------|
| `OTLP_HTTP_ENDPOINT` | `http://otel-collector.bigip-metrics.svc.cluster.local:4318` | OTLP push target |
| `PROMETHEUS_UI_URL` | `http://prometheus.bigip-metrics.svc.cluster.local:9090` | Link in UI |
| `COLLECTOR_METRICS_URL` | `http://otel-collector...:8889/metrics` | Validation link |
| `COLLECTOR_CONFIG_PATH` | `/tmp/collector-config.yaml` | Where API writes generated YAML |
| `COLLECTOR_RESTART_HINT` | `kubectl ... rollout restart ...` | Shown after Apply in UI |

## BIG-IP network connectivity

The exporter pod must reach BIG-IP management interfaces. Common patterns:

- BIG-IP on a routable network from worker nodes.
- Cloud hybrid / VPN / transit gateway.
- `hostNetwork: true` on the backend Deployment (only if required by your CNI/security policy)—add via a custom overlay patch.

If connection fails from the UI but works locally on your laptop, debug with:

```bash
kubectl -n bigip-metrics exec -it deploy/bigip-metrics-backend -- \
  python -c "import urllib.request; urllib.request.urlopen('https://<BIG-IP-MGMT-IP>/mgmt/shared/ident', context=__import__('ssl')._create_unverified_context())"
```

## Upgrades

```bash
./scripts/k8s-build-image.sh
docker push <your-image>:<tag>
# Update image tag in overlay kustomization.yaml
kubectl apply -k k8s/overlays/minimal
kubectl -n bigip-metrics rollout status deployment/bigip-metrics-backend
```

## Uninstall

```bash
kubectl delete -k k8s/overlays/minimal
# Namespace is removed with the overlay resources
```

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `ErrImagePull` / `authorization failed` for `bigip-metrics-exporter` | Image is not on Docker Hub. Use `./scripts/k8s-deploy.sh local` after build+load, or `IMAGE=<registry>/... ./scripts/k8s-deploy.sh minimal` after push |
| Backend `ImagePullBackOff` | Same as above; `kubectl describe pod` → Events |
| No metrics in Prometheus | `kubectl logs deploy/otel-collector`; export started in UI? |
| OTLP errors in backend logs | Service `otel-collector` endpoints; port 4318 |
| BIG-IP login fails | Network/firewall; TLS verify setting |
| Ingress 404 | Host/DNS; `ingressClassName`; backend service port 8000 |
