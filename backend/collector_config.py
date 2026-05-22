"""Generate OpenTelemetry Collector YAML from UI exporter selections."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "otel-collector" / "config.yaml"
GENERATED_CONFIG_PATH = Path(
    os.environ.get("COLLECTOR_CONFIG_PATH", str(REPO_ROOT / "otel-collector" / "generated-config.yaml")),
)

EXPORTER_TYPES = {
    "prometheus": {
        "label": "Prometheus (expose :8889 for scrape)",
        "description": "Built-in validation exporter; scraped by local Prometheus.",
    },
    "otlp_http": {
        "label": "OTLP HTTP",
        "description": "Forward metrics to an OTLP/HTTP endpoint.",
    },
    "otlp_grpc": {
        "label": "OTLP gRPC",
        "description": "Forward metrics to an OTLP/gRPC endpoint.",
    },
    "debug": {
        "label": "Debug",
        "description": "Log telemetry to collector stdout (verbose).",
    },
    "file": {
        "label": "File (JSON)",
        "description": "Write metrics to a JSON file inside the collector container.",
    },
}


def _exporter_block(exporter_type: str, params: dict[str, Any]) -> dict[str, Any]:
    if exporter_type == "prometheus":
        return {
            "endpoint": params.get("endpoint", "0.0.0.0:8889"),
            "const_labels": params.get("const_labels", {"source": "bigip-metrics-exporter"}),
        }
    if exporter_type == "otlp_http":
        endpoint = params.get("endpoint", "http://localhost:4318")
        return {
            "metrics_endpoint": params.get(
                "metrics_endpoint",
                f"{endpoint.rstrip('/')}/v1/metrics",
            ),
            "compression": params.get("compression", "gzip"),
        }
    if exporter_type == "otlp_grpc":
        endpoint = params.get("endpoint", "localhost:4317")
        return {
            "endpoint": endpoint,
            "compression": params.get("compression", "gzip"),
            "tls": {"insecure": params.get("insecure", True)},
        }
    if exporter_type == "debug":
        return {
            "verbosity": params.get("verbosity", "basic"),
            "sampling_initial": 5,
            "sampling_thereafter": 200,
        }
    if exporter_type == "file":
        return {
            "path": params.get("path", "/tmp/bigip-metrics.json"),
            "rotation": {"max_megabytes": 10, "max_days": 1, "max_backups": 2},
        }
    raise ValueError(f"Unsupported exporter type: {exporter_type}")


def build_collector_config(
    selected_exporters: list[dict[str, Any]],
    *,
    include_validation_prometheus: bool = True,
) -> dict[str, Any]:
    """
    selected_exporters: [{type, params, enabled}]
    Always includes OTLP receivers for the Python backend.
    """
    exporters: dict[str, Any] = {}
    metric_exporters: list[str] = []

    if include_validation_prometheus:
        exporters["prometheus/validation"] = _exporter_block("prometheus", {"endpoint": "0.0.0.0:8889"})
        metric_exporters.append("prometheus/validation")

    type_aliases = {
        "otlp_http": "otlphttp",
        "otlp_grpc": "otlp",
    }
    for idx, item in enumerate(selected_exporters):
        if not item.get("enabled", True):
            continue
        etype = item["type"]
        params = item.get("params") or {}
        component = type_aliases.get(etype, etype)
        key = f"{component}/{idx}"
        exporters[key] = _exporter_block(etype, params)
        metric_exporters.append(key)

    if not metric_exporters:
        exporters["prometheus/validation"] = _exporter_block("prometheus", {})
        metric_exporters.append("prometheus/validation")

    config = {
        "receivers": {
            "otlp": {
                "protocols": {
                    "grpc": {"endpoint": "0.0.0.0:4317"},
                    "http": {"endpoint": "0.0.0.0:4318"},
                }
            }
        },
        "processors": {
            "batch": {"timeout": "5s", "send_batch_size": 512},
            "memory_limiter": {
                "check_interval": "1s",
                "limit_mib": 512,
                "spike_limit_mib": 128,
            },
        },
        "exporters": exporters,
        "extensions": {"health_check": {"endpoint": "0.0.0.0:13133"}},
        "service": {
            "extensions": ["health_check"],
            "pipelines": {
                "metrics": {
                    "receivers": ["otlp"],
                    "processors": ["memory_limiter", "batch"],
                    "exporters": metric_exporters,
                }
            },
            "telemetry": {
                "logs": {"level": "info"},
                "metrics": {"level": "basic"},
            },
        },
    }
    return config


def write_collector_config(
    selected_exporters: list[dict[str, Any]],
    *,
    path: Path | None = None,
) -> Path:
    path = path or GENERATED_CONFIG_PATH
    config = build_collector_config(selected_exporters)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, default_flow_style=False)
    return path


def list_exporter_catalog() -> list[dict[str, str]]:
    return [
        {"type": k, **v}
        for k, v in EXPORTER_TYPES.items()
    ]
