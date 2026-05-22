"""Push extracted metrics to an OTLP HTTP endpoint (OpenTelemetry Collector)."""

from __future__ import annotations

import time
from typing import Any

from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource


class OTLPMetricsPusher:
    def __init__(self, endpoint: str, *, interval_ms: int = 5000) -> None:
        endpoint = endpoint.rstrip("/")
        if not endpoint.endswith("/v1/metrics"):
            endpoint = f"{endpoint}/v1/metrics"
        resource = Resource.create(
            {
                "service.name": "bigip-metrics-exporter",
                "service.namespace": "f5",
            }
        )
        exporter = OTLPMetricExporter(endpoint=endpoint)
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=interval_ms,
        )
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(provider)
        self._meter = metrics.get_meter("bigip.metrics")
        self._instruments: dict[str, Any] = {}
        self._provider = provider

    def _gauge(self, name: str):
        if name not in self._instruments:
            safe = name.replace(".", "_")[:120]
            self._instruments[name] = self._meter.create_observable_gauge(
                safe,
                callbacks=[],
            )
        return self._instruments[name]

    def record_batch(self, points: list[dict[str, Any]]) -> int:
        """Record metrics using synchronous gauge observations via manual export trigger."""
        # SDK observable gauges need callbacks; use UpDownCounter as cumulative stand-in for stats snapshots.
        count = 0
        for pt in points:
            name = pt["name"]
            key = name
            if key not in self._instruments:
                self._instruments[key] = self._meter.create_up_down_counter(
                    name.replace(".", "_")[:120],
                    description=f"BIG-IP metric {name}",
                )
            inst = self._instruments[key]
            inst.add(int(pt["value"]) if float(pt["value"]).is_integer() else pt["value"])
            count += 1
        return count

    def force_flush(self, timeout_ms: int = 10000) -> bool:
        return self._provider.force_flush(timeout_millis=timeout_ms)

    def shutdown(self) -> None:
        self._provider.shutdown()


class MetricsExportLoop:
    def __init__(
        self,
        client: Any,
        endpoints: list[str],
        pusher: OTLPMetricsPusher,
        *,
        poll_interval_sec: float = 30.0,
    ) -> None:
        self._client = client
        self._endpoints = endpoints
        self._pusher = pusher
        self._poll_interval_sec = poll_interval_sec
        self._running = False
        self._last_run: float | None = None
        self._last_error: str | None = None
        self._last_point_count = 0

    @property
    def status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "endpoints": len(self._endpoints),
            "last_run": self._last_run,
            "last_error": self._last_error,
            "last_point_count": self._last_point_count,
            "poll_interval_sec": self._poll_interval_sec,
        }

    def run_once(self) -> dict[str, Any]:
        from .metrics_extractor import extract_metrics

        total = 0
        errors: list[str] = []
        for ep in self._endpoints:
            try:
                payload = self._client.get(ep)
                points = extract_metrics(ep, payload)
                total += self._pusher.record_batch(points)
            except Exception as exc:  # noqa: BLE001 — collect per-endpoint errors
                errors.append(f"{ep}: {exc}")
        self._pusher.force_flush()
        self._last_run = time.time()
        self._last_point_count = total
        self._last_error = "; ".join(errors[:5]) if errors else None
        return {"points": total, "errors": errors}

    def start_background(self) -> None:
        import threading

        if self._running:
            return
        self._running = True

        def _loop() -> None:
            while self._running:
                try:
                    self.run_once()
                except Exception as exc:  # noqa: BLE001
                    self._last_error = str(exc)
                time.sleep(self._poll_interval_sec)

        t = threading.Thread(target=_loop, name="bigip-export-loop", daemon=True)
        t.start()

    def stop(self) -> None:
        self._running = False
