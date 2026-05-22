"""FastAPI service: BIG-IP polling, OTLP export, collector configuration."""

from __future__ import annotations

import csv
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.bigip_client import BigIPClient, BigIPError
from backend.collector_config import (
    EXPORTER_TYPES,
    GENERATED_CONFIG_PATH,
    build_collector_config,
    list_exporter_catalog,
    write_collector_config,
)
from backend.metrics_extractor import extract_metrics
from backend.otel_export import MetricsExportLoop, OTLPMetricsPusher

REPO_ROOT = Path(__file__).resolve().parent.parent
APIS_CSV = REPO_ROOT / "data" / "bigip_apis.csv"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

SESSION_TTL_SEC = 45 * 60


@dataclass
class _Session:
    client: BigIPClient
    created: float


_sessions: dict[str, _Session] = {}
_export_loop: MetricsExportLoop | None = None
_pusher: OTLPMetricsPusher | None = None
_collector_exporters: list[dict[str, Any]] = [
    {"type": "prometheus", "enabled": True, "params": {"endpoint": "0.0.0.0:8889"}},
]


def _gc_sessions() -> None:
    now = time.time()
    for sid, s in list(_sessions.items()):
        if now - s.created > SESSION_TTL_SEC:
            try:
                s.client.logout()
            except Exception:
                pass
            _sessions.pop(sid, None)


def _get_session(session_id: str) -> _Session:
    _gc_sessions()
    s = _sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Unknown or expired session")
    return s


def _load_apis() -> list[dict[str, str]]:
    if not APIS_CSV.exists():
        return []
    with APIS_CSV.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class ConnectBody(BaseModel):
    host: str
    username: str
    password: str
    verify_tls: bool = False


class ExporterItem(BaseModel):
    type: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class CollectorConfigBody(BaseModel):
    exporters: list[ExporterItem]
    restart_hint: bool = Field(
        default=True,
        description="When true, response includes docker compose restart command",
    )


class ExportStartBody(BaseModel):
    endpoints: list[str] = Field(default_factory=list)
    metrics_only: bool = True
    modules: list[str] = Field(default_factory=list)
    poll_interval_sec: float = 30.0
    otlp_endpoint: str = Field(
        default="http://127.0.0.1:4318",
        description="OTLP HTTP base URL for the collector (backend pushes here)",
    )


class ProbeBody(BaseModel):
    endpoint: str


app = FastAPI(title="BIG-IP Metrics Exporter", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/apis")
def list_apis(
    metrics_only: bool = False,
    module: str | None = None,
) -> dict[str, Any]:
    rows = _load_apis()
    if metrics_only:
        rows = [r for r in rows if r.get("collect_metrics", "").lower() == "true"]
    if module:
        mod = module.upper()
        rows = [r for r in rows if (r.get("module") or "").upper() == mod]
    modules = sorted({(r.get("module") or "").upper() for r in _load_apis() if r.get("module")})
    return {"apis": rows, "count": len(rows), "modules": modules}


@app.post("/api/connect")
def connect(body: ConnectBody) -> dict[str, str]:
    client = BigIPClient(
        body.host,
        body.username,
        body.password,
        verify_tls=body.verify_tls,
    )
    try:
        client.login()
        client.extend_token()
    except BigIPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    sid = secrets.token_urlsafe(24)
    _sessions[sid] = _Session(client=client, created=time.time())
    return {"session_id": sid, "host": body.host}


@app.delete("/api/session/{session_id}")
def disconnect(session_id: str) -> dict[str, bool]:
    s = _sessions.pop(session_id, None)
    if s:
        try:
            s.client.logout()
        except Exception:
            pass
    return {"ok": True}


@app.post("/api/probe")
def probe_endpoint(body: ProbeBody, session_id: str = Query(...)) -> dict[str, Any]:
    s = _get_session(session_id)
    try:
        payload = s.client.get(body.endpoint)
        points = extract_metrics(body.endpoint, payload)
        return {
            "endpoint": body.endpoint,
            "ok": True,
            "metric_points": len(points),
            "sample": points[:5],
        }
    except BigIPError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/exporters/catalog")
def exporters_catalog() -> dict[str, Any]:
    return {"exporters": list_exporter_catalog(), "types": list(EXPORTER_TYPES)}


@app.get("/api/collector/config")
def get_collector_config() -> dict[str, Any]:
    cfg = build_collector_config(_collector_exporters)
    return {
        "exporters": _collector_exporters,
        "yaml": _yaml_dump(cfg),
        "generated_path": str(GENERATED_CONFIG_PATH),
    }


@app.post("/api/collector/config")
def apply_collector_config(body: CollectorConfigBody) -> dict[str, Any]:
    global _collector_exporters
    _collector_exporters = [e.model_dump() for e in body.exporters]
    path = write_collector_config(_collector_exporters)
    cfg = build_collector_config(_collector_exporters)
    return {
        "ok": True,
        "path": str(path),
        "yaml": _yaml_dump(cfg),
        "restart_command": "docker compose restart otel-collector",
    }


@app.get("/api/export/status")
def export_status() -> dict[str, Any]:
    global _export_loop, _pusher
    return {
        "loop": _export_loop.status if _export_loop else {"running": False},
        "otlp_endpoint": getattr(_pusher, "_endpoint", None) if _pusher else None,
    }


@app.post("/api/export/start")
def export_start(body: ExportStartBody, session_id: str = Query(...)) -> dict[str, Any]:
    global _export_loop, _pusher
    s = _get_session(session_id)
    endpoints = body.endpoints
    if not endpoints:
        rows = _load_apis()
        if body.metrics_only:
            rows = [r for r in rows if r.get("collect_metrics", "").lower() == "true"]
        if body.modules:
            mods = {m.upper() for m in body.modules}
            rows = [r for r in rows if (r.get("module") or "").upper() in mods]
        endpoints = [r["endpoint"] for r in rows]
    if not endpoints:
        raise HTTPException(status_code=400, detail="No endpoints selected")

    if _export_loop and _export_loop.status.get("running"):
        _export_loop.stop()
    if _pusher:
        _pusher.shutdown()

    otlp = body.otlp_endpoint.rstrip("/")
    _pusher = OTLPMetricsPusher(otlp)
    _export_loop = MetricsExportLoop(
        s.client,
        endpoints,
        _pusher,
        poll_interval_sec=body.poll_interval_sec,
    )
    result = _export_loop.run_once()
    _export_loop.start_background()
    return {
        "started": True,
        "endpoints": len(endpoints),
        "first_run": result,
        "status": _export_loop.status,
    }


@app.post("/api/export/stop")
def export_stop() -> dict[str, Any]:
    global _export_loop, _pusher
    if _export_loop:
        _export_loop.stop()
    if _pusher:
        _pusher.shutdown()
    _export_loop = None
    _pusher = None
    return {"stopped": True}


@app.get("/api/validation/prometheus")
def prometheus_hints() -> dict[str, str]:
    return {
        "prometheus_ui": "http://127.0.0.1:9090",
        "collector_metrics": "http://127.0.0.1:8889/metrics",
        "query_example": 'bigip_tm_ltm_virtual_stats',
        "scrape_job": "otel-collector",
    }


def _yaml_dump(cfg: dict[str, Any]) -> str:
    import yaml

    return yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False)


def _mount_frontend() -> None:
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="ui")


_mount_frontend()
