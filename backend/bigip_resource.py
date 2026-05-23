"""Shared helpers for idempotent BIG-IP configuration objects."""

from __future__ import annotations

from typing import Any

from backend.bigip_client import BigIPClient, BigIPError


def is_not_found(exc: BigIPError) -> bool:
    return "404" in str(exc)


def ensure_config_object(
    client: BigIPClient,
    *,
    collection_path: str,
    instance_path: str,
    create_body: dict[str, Any],
    patch_body: dict[str, Any],
) -> bool:
    """Create the object if missing, otherwise patch. Returns True when newly created."""
    try:
        client.get(instance_path)
    except BigIPError as exc:
        if not is_not_found(exc):
            raise
        client.post(collection_path, json_body=create_body)
        return True

    client.patch(instance_path, json_body=patch_body)
    return False
