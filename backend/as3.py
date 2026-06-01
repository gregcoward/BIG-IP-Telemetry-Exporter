"""BIG-IP AS3 (appsvcs) install check and declaration deploy."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from backend.bigip_client import BigIPClient, BigIPError, format_icontrol_error
from backend.bigip_resource import is_not_found, path_from_self_link

logger = logging.getLogger(__name__)

AS3_INFO_PATH = "/mgmt/shared/appsvcs/info"
AS3_DECLARE_PATH = "/mgmt/shared/appsvcs/declare"
PACKAGE_TASKS_PATH = "/mgmt/shared/iapp/package-management-tasks"
DEFAULT_SCHEMA_VERSION = "3.49.0"
DEFAULT_INSTALL_TIMEOUT_SEC = 600
DEFAULT_PACKAGE_POLL_SEC = 2.0
DEFAULT_GITHUB_REPO = "F5Networks/f5-appsvcs-extension"
DEFAULT_DOWNLOAD_CACHE_DIR = Path.home() / ".cache" / "bigip-telemetry-exporter" / "as3-rpms"
RPM_NAME_RE = re.compile(r"^f5-appsvcs-.*\.noarch\.rpm$")


def _schema_version() -> str:
    return os.environ.get("BIGIP_AS3_SCHEMA_VERSION", DEFAULT_SCHEMA_VERSION).strip()


def _rpm_path() -> str:
    return os.environ.get("BIGIP_AS3_RPM_PATH", "").strip()


def _auto_install_enabled() -> bool:
    return os.environ.get("BIGIP_AS3_AUTO_INSTALL", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _install_timeout_sec() -> int:
    raw = os.environ.get("BIGIP_AS3_INSTALL_TIMEOUT_SEC", str(DEFAULT_INSTALL_TIMEOUT_SEC)).strip()
    return int(raw)


def _github_repo() -> str:
    return os.environ.get("BIGIP_AS3_GITHUB_REPO", DEFAULT_GITHUB_REPO).strip() or DEFAULT_GITHUB_REPO


def _release_version() -> str:
    return os.environ.get("BIGIP_AS3_RELEASE_VERSION", "latest").strip() or "latest"


def _download_cache_dir() -> Path:
    raw = os.environ.get("BIGIP_AS3_DOWNLOAD_CACHE_DIR", "").strip()
    return Path(raw) if raw else DEFAULT_DOWNLOAD_CACHE_DIR


def _github_download_enabled() -> bool:
    return os.environ.get("BIGIP_AS3_GITHUB_DOWNLOAD", "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _github_release_url() -> str:
    repo = _github_repo()
    version = _release_version()
    base = f"https://api.github.com/repos/{repo}/releases"
    if version.lower() == "latest":
        return f"{base}/latest"
    tag = version if version.startswith("v") else f"v{version}"
    return f"{base}/tags/{tag}"


def _fetch_github_release() -> dict[str, Any]:
    url = _github_release_url()
    try:
        response = requests.get(
            url,
            timeout=60,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BigIPError(
            f"Could not fetch AS3 release metadata from GitHub ({url}): {exc}"
        ) from exc
    data = response.json()
    if not isinstance(data, dict):
        raise BigIPError("Unexpected GitHub release response for AS3")
    return data


def _rpm_asset_from_release(release: dict[str, Any]) -> tuple[str, str]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise BigIPError("GitHub AS3 release has no downloadable assets")
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", "")).strip()
        download_url = str(asset.get("browser_download_url", "")).strip()
        if RPM_NAME_RE.match(name) and download_url:
            return name, download_url
    tag = release.get("tag_name") or release.get("name") or "unknown"
    raise BigIPError(
        f"No f5-appsvcs-*.noarch.rpm asset found in GitHub release {tag} "
        f"(repo {_github_repo()})."
    )


def download_as3_rpm_from_github() -> Path:
    """Download the AS3 RPM from the official GitHub releases (cached locally)."""
    release = _fetch_github_release()
    name, download_url = _rpm_asset_from_release(release)
    cache_dir = _download_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / name
    if dest.is_file() and dest.stat().st_size > 0:
        logger.info("Using cached AS3 RPM at %s", dest)
        return dest

    logger.info("Downloading AS3 RPM %s from GitHub", name)
    partial = dest.with_suffix(dest.suffix + ".part")
    try:
        with requests.get(download_url, timeout=600, stream=True) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        partial.replace(dest)
    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)
        raise BigIPError(f"Failed to download AS3 RPM from GitHub: {exc}") from exc
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise BigIPError(f"Failed to write AS3 RPM to {dest}: {exc}") from exc

    if not dest.is_file() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise BigIPError(f"Downloaded AS3 RPM at {dest} is empty")
    return dest


def resolve_as3_rpm_path() -> Path:
    """Return a local AS3 RPM path, downloading from GitHub when configured."""
    local = _rpm_path()
    if local:
        return Path(local)
    if not _github_download_enabled():
        raise BigIPError(
            "AS3 is not installed. Set BIGIP_AS3_RPM_PATH to a local f5-appsvcs-*.noarch.rpm "
            "file, or enable BIGIP_AS3_GITHUB_DOWNLOAD to fetch from GitHub releases."
        )
    return download_as3_rpm_from_github()


def as3_info(client: BigIPClient) -> dict[str, Any] | None:
    """Return AS3 info document when appsvcs is installed, else None."""
    try:
        data = client.get(AS3_INFO_PATH)
    except BigIPError as exc:
        if is_not_found(exc) or "404" in str(exc):
            return None
        raise
    return data if isinstance(data, dict) else None


def _wait_package_task(client: BigIPClient, task: dict[str, Any]) -> None:
    link = path_from_self_link(str(task.get("selfLink", "")))
    if not link:
        raise BigIPError("AS3 package install task missing selfLink")
    deadline = time.time() + _install_timeout_sec()
    while time.time() < deadline:
        state = client.get(link)
        if not isinstance(state, dict):
            raise BigIPError("Invalid package task status response")
        status = str(state.get("status", "")).upper()
        if status == "FINISHED":
            err = state.get("error")
            if err:
                raise BigIPError(f"AS3 package install failed: {err}")
            return
        if status in ("FAILED", "ERROR", "CANCELLED"):
            raise BigIPError(
                f"AS3 package install {status.lower()}: "
                f"{state.get('error') or state.get('message') or state}",
            )
        time.sleep(DEFAULT_PACKAGE_POLL_SEC)
    raise BigIPError("Timed out waiting for AS3 RPM installation")


def install_as3_rpm(client: BigIPClient, rpm_file: Path) -> None:
    """Upload and install an f5-appsvcs RPM on the BIG-IP."""
    if not rpm_file.is_file():
        raise BigIPError(f"AS3 RPM not found: {rpm_file}")
    name = rpm_file.name
    client.upload_bytes(f"/mgmt/shared/file-transfer/uploads/{name}", rpm_file.read_bytes())
    task = client.post(
        PACKAGE_TASKS_PATH,
        json_body={
            "operation": "INSTALL",
            "packageFilePath": f"/var/config/rest/downloads/{name}",
        },
    )
    if not isinstance(task, dict):
        raise BigIPError("Unexpected response from package-management-tasks")
    _wait_package_task(client, task)
    if as3_info(client) is None:
        raise BigIPError("AS3 RPM install finished but /mgmt/shared/appsvcs/info is unavailable")


def ensure_as3_available(client: BigIPClient) -> dict[str, Any]:
    """Verify AS3 is installed; optionally install from local path or GitHub release."""
    info = as3_info(client)
    if info is not None:
        return info
    if not _auto_install_enabled():
        raise BigIPError(
            "F5 Application Services (AS3) is not installed on this BIG-IP. "
            "Install the f5-appsvcs RPM or set BIGIP_AS3_AUTO_INSTALL=true."
        )
    rpm_path = resolve_as3_rpm_path()
    install_as3_rpm(client, rpm_path)
    info = as3_info(client)
    if info is None:
        raise BigIPError("AS3 is still unavailable after RPM installation")
    return info


def _declaration_failed(response: dict[str, Any]) -> bool:
    if response.get("message") in ("declaration is invalid", "declaration failed"):
        return True
    errors = response.get("errors")
    if isinstance(errors, list) and errors:
        return True
    results = response.get("results")
    if isinstance(results, list):
        for entry in results:
            if isinstance(entry, dict) and int(entry.get("code", 200)) >= 400:
                return True
    return False


def post_declaration(client: BigIPClient, declaration: dict[str, Any]) -> dict[str, Any]:
    """POST an AS3 declaration (deploy)."""
    response = client.post(AS3_DECLARE_PATH, json_body=declaration)
    if not isinstance(response, dict):
        return {}
    if _declaration_failed(response):
        raise BigIPError(format_icontrol_error(422, json.dumps(response)))
    return response


def schema_version_for_declaration(client: BigIPClient) -> str:
    info = as3_info(client)
    if info:
        for key in ("schemaCurrent", "schemaVersion"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return _schema_version()
