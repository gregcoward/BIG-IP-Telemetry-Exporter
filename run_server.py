#!/usr/bin/env python3
"""Run the BIG-IP Metrics Exporter API."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
