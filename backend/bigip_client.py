"""iControl REST client for BIG-IP management APIs."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import requests
import urllib3


class BigIPError(Exception):
    pass


class BigIPClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = False,
        timeout: int = 60,
    ) -> None:
        host = host.strip().rstrip("/")
        if host.startswith("http://") or host.startswith("https://"):
            self.base = host
        else:
            self.base = f"https://{host}"
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self.timeout = timeout
        self._token: str | None = None
        self._session = requests.Session()
        if not verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return urljoin(self.base + "/", path.lstrip("/"))

    def login(self) -> None:
        url = self._url("/mgmt/shared/authn/login")
        payload = {"username": self.username, "password": self.password}
        r = self._session.post(
            url,
            json=payload,
            verify=self.verify_tls,
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise BigIPError(f"Login failed ({r.status_code}): {r.text[:400]}")
        data = r.json()
        token = data.get("token", {}).get("token")
        if not token:
            raise BigIPError("Login response missing token")
        self._token = token
        self._session.headers["X-F5-Auth-Token"] = token

    def extend_token(self) -> None:
        if not self._token:
            return
        url = self._url("/mgmt/shared/authz/tokens")
        r = self._session.patch(
            url,
            json={"timeout": 1200},
            verify=self.verify_tls,
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise BigIPError(f"Token extension failed ({r.status_code})")

    def get(self, endpoint: str, *, params: dict[str, Any] | None = None) -> Any:
        if not self._token:
            self.login()
        url = self._url(endpoint)
        r = self._session.get(
            url,
            params=params,
            verify=self.verify_tls,
            timeout=self.timeout,
        )
        if r.status_code == 401:
            self.login()
            r = self._session.get(
                url,
                params=params,
                verify=self.verify_tls,
                timeout=self.timeout,
            )
        if r.status_code >= 400:
            raise BigIPError(f"GET {endpoint} failed ({r.status_code}): {r.text[:400]}")
        if not r.text.strip():
            return {}
        try:
            return r.json()
        except json.JSONDecodeError as exc:
            raise BigIPError(f"Non-JSON response from {endpoint}") from exc

    def logout(self) -> None:
        if not self._token:
            return
        try:
            self._session.delete(
                self._url(f"/mgmt/shared/authz/tokens/{self._token}"),
                verify=self.verify_tls,
                timeout=10,
            )
        except requests.RequestException:
            pass
        finally:
            self._token = None
            self._session.headers.pop("X-F5-Auth-Token", None)
