"""Remove exporter-managed log resources from a BIG-IP device."""

from __future__ import annotations

from typing import Any

from backend.as3_log_profiles import delete_log_profiles_application
from backend.bigip_client import BigIPClient, BigIPError
from backend.system_syslog import remove_system_syslog_forwarding


def session_has_log_resources(sess: Any) -> bool:
    """True when the session tracks deployed log profiles or syslog forwarding."""
    return bool(
        getattr(sess, "request_log_profile", None)
        or getattr(sess, "asm_log_profile", None)
        or getattr(sess, "afm_log_profile", None)
        or getattr(sess, "http_analytics_profile", None)
        or getattr(sess, "tcp_analytics_profile", None)
        or getattr(sess, "system_syslog_target", None)
        or getattr(sess, "log_syslog_target", None)
        or getattr(sess, "log_hsl_target", None)
        or getattr(sess, "export_system_logs", False)
        or getattr(sess, "export_logs", False)
    )


def clear_session_log_resources(sess: Any) -> None:
    """Clear session fields after a successful BIG-IP log rollback."""
    sess.request_log_profile = None
    sess.request_log_profile_created = None
    sess.asm_log_profile = None
    sess.asm_log_profile_created = None
    sess.afm_log_profile = None
    sess.afm_log_profile_created = None
    sess.http_analytics_profile = None
    sess.http_analytics_profile_created = None
    sess.tcp_analytics_profile = None
    sess.tcp_analytics_profile_created = None
    sess.log_syslog_target = None
    sess.log_hsl_target = None
    sess.system_syslog_target = None
    sess.export_system_logs = False
    sess.export_ltm_logs = False
    sess.export_asm_logs = False
    sess.export_afm_logs = False
    sess.export_avr_logs = False


def rollback_log_resources(
    client: BigIPClient,
    *,
    delete_as3: bool = True,
    remove_system_syslog: bool = True,
    save_sys_config_after: bool = True,
) -> list[dict[str, Any]]:
    """
    Best-effort removal of exporter AS3 log profiles and system syslog forwarding.

    Raises BigIPError when a step fails (404 on AS3 delete is treated as success).
    """
    steps: list[dict[str, Any]] = []

    if delete_as3:
        result = delete_log_profiles_application(client)
        steps.append({"step": "as3_delete_application", **result})

    if remove_system_syslog:
        result = remove_system_syslog_forwarding(client)
        steps.append({"step": "system_syslog_remove", **result})

    if save_sys_config_after:
        save_resp = client.save_sys_config()
        steps.append({"step": "save_sys_config", "response": save_resp})

    return steps
