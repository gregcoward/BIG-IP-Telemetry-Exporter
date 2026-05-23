"""ASM and AFM security log profiles via /mgmt/tm/security/log/profile."""

from __future__ import annotations

import os
from dataclasses import dataclass

from backend.bigip_client import BigIPClient, BigIPError
from backend.bigip_resource import ensure_config_object, is_not_found
from backend.log_templates import REQUEST_EVENT_TEMPLATE
from backend.module_provision import is_module_provisioned

# Re-export for tests and documentation.
__all__ = [
    "SecurityLogProfileResult",
    "ensure_afm_log_profile",
    "ensure_asm_log_profile",
]

SECURITY_LOG_PROFILE_COLLECTION = "/mgmt/tm/security/log/profile"
DEFAULT_PARTITION = "Common"
DEFAULT_ASM_NAME = "bigip-metrics-asm-log"
DEFAULT_AFM_NAME = "bigip-metrics-afm-log"
DEFAULT_AFM_LOG_PUBLISHER = "/Common/local-db-publisher"
DEFAULT_AFM_AGGREGATE_RATE_LIMIT = 1000
ASM_DESCRIPTION = (
    "Created by BIG-IP Metrics Exporter. Attach as a Security Log Profile on virtual servers "
    "for ASM (Application Security); logs all requests and responses locally (request-type all) "
    "for future OTLP export."
)
AFM_DESCRIPTION = (
    "Created by BIG-IP Metrics Exporter. Attach as a Security Log Profile on virtual servers "
    "for AFM (Network Firewall); logs ACL matches and network events for future OTLP export."
)


@dataclass(frozen=True)
class SecurityLogProfileResult:
    full_name: str
    instance_path: str
    created: bool
    module: str


def _partition() -> str:
    return os.environ.get("BIGIP_LOG_PROFILE_PARTITION", DEFAULT_PARTITION).strip() or "Common"


def _asm_name() -> str:
    return os.environ.get("BIGIP_ASM_LOG_PROFILE_NAME", DEFAULT_ASM_NAME).strip()


def _afm_name() -> str:
    return os.environ.get("BIGIP_AFM_LOG_PROFILE_NAME", DEFAULT_AFM_NAME).strip()


def _afm_log_publisher() -> str:
    return os.environ.get("BIGIP_AFM_LOG_PUBLISHER", DEFAULT_AFM_LOG_PUBLISHER).strip()


def _afm_aggregate_rate_limit() -> int:
    raw = os.environ.get(
        "BIGIP_AFM_AGGREGATE_RATE_LIMIT",
        str(DEFAULT_AFM_AGGREGATE_RATE_LIMIT),
    ).strip()
    return int(raw)


def _full_name(partition: str, name: str) -> str:
    return f"/{partition}/{name}"


def _security_log_profile_path(partition: str, name: str) -> str:
    return f"{SECURITY_LOG_PROFILE_COLLECTION}/~{partition}~{name}"


def _asm_auto_create() -> bool:
    return os.environ.get("BIGIP_ASM_LOG_AUTO_CREATE", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _afm_auto_create() -> bool:
    return os.environ.get("BIGIP_AFM_LOG_AUTO_CREATE", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _asm_application_body() -> dict:
    """Application sub-profile; request-type all via storageFilter.requestType (not filter/name)."""
    return {
        "name": "application",
        "localStorage": "enabled",
        "guaranteeLogging": "enabled",
        "guaranteeResponseLogging": "enabled",
        "responseLogging": "all",
        "logicOperation": "or",
        "storageFilter": {
            "requestType": "all",
        },
        "format": {
            "type": "user-defined",
            "userString": REQUEST_EVENT_TEMPLATE,
        },
    }


def _profile_shell(*, partition: str, name: str, description: str) -> dict:
    return {"name": name, "partition": partition, "description": description}


def _ensure_log_profile_subcollection(
    client: BigIPClient,
    *,
    partition: str,
    name: str,
    description: str,
    subcollection: str,
    sub_body: dict[str, object],
) -> bool:
    """Create security log profile shell, then configure application or network sub-profile."""
    path = _security_log_profile_path(partition, name)
    shell = _profile_shell(partition=partition, name=name, description=description)
    created = ensure_config_object(
        client,
        collection_path=SECURITY_LOG_PROFILE_COLLECTION,
        instance_path=path,
        create_body=shell,
        patch_body={"description": description},
    )

    sub_instance = f"{path}/{subcollection}/{subcollection}"
    sub_collection = f"{path}/{subcollection}"
    try:
        client.get(sub_instance)
    except BigIPError as exc:
        if not is_not_found(exc):
            raise
        client.post(sub_collection, json_body=sub_body)
        return created

    client.patch(sub_instance, json_body=sub_body)
    return created


def ensure_asm_log_profile(
    client: BigIPClient,
    *,
    partition: str | None = None,
    name: str | None = None,
) -> SecurityLogProfileResult | None:
    """ASM security log profile with application sub-profile (request-type all)."""
    if not is_module_provisioned(client, "asm"):
        return None
    part = partition or _partition()
    prof = name or _asm_name()
    path = _security_log_profile_path(part, prof)
    full = _full_name(part, prof)
    if not _asm_auto_create():
        return SecurityLogProfileResult(
            full_name=full,
            instance_path=path,
            created=False,
            module="ASM",
        )

    created = _ensure_log_profile_subcollection(
        client,
        partition=part,
        name=prof,
        description=ASM_DESCRIPTION,
        subcollection="application",
        sub_body=_asm_application_body(),
    )
    return SecurityLogProfileResult(
        full_name=full, instance_path=path, created=created, module="ASM"
    )


def _afm_network_settings() -> dict:
    """Network sub-profile for POST /mgmt/tm/security/log/profile."""
    return {
        "logPublisher": _afm_log_publisher(),
        "logRuleMatches": ["accept", "drop", "reject"],
        "logIpErrors": "enabled",
        "logTcpErrors": "enabled",
        "logTcpEvents": "enabled",
        "logTranslationFields": "enabled",
        "aggregateRateLimit": _afm_aggregate_rate_limit(),
    }


def _afm_profile_settings(*, partition: str, name: str) -> dict:
    return {
        "name": name,
        "partition": partition,
        "description": AFM_DESCRIPTION,
        "network": _afm_network_settings(),
    }


def _afm_patch_settings() -> dict:
    return {
        "description": AFM_DESCRIPTION,
        "network": _afm_network_settings(),
    }


def ensure_afm_log_profile(
    client: BigIPClient,
    *,
    partition: str | None = None,
    name: str | None = None,
) -> SecurityLogProfileResult | None:
    """AFM security log profile via /mgmt/tm/security/log/profile (network firewall logging)."""
    if not is_module_provisioned(client, "afm"):
        return None
    part = partition or _partition()
    prof = name or _afm_name()
    full = _full_name(part, prof)
    path = _security_log_profile_path(part, prof)
    if not _afm_auto_create():
        return SecurityLogProfileResult(
            full_name=full,
            instance_path=path,
            created=False,
            module="AFM",
        )

    created = ensure_config_object(
        client,
        collection_path=SECURITY_LOG_PROFILE_COLLECTION,
        instance_path=path,
        create_body=_afm_profile_settings(partition=part, name=prof),
        patch_body=_afm_patch_settings(),
    )
    return SecurityLogProfileResult(
        full_name=full, instance_path=path, created=created, module="AFM"
    )
