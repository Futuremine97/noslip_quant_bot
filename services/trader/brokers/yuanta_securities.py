from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from .base import (
    BrokerApiError,
    BrokerConfigError,
    BrokerMode,
    OrderRequest,
    validate_symbols,
)
from .http import RequestsTransport, response_json


DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"


class YuantaSecuritiesClient:
    """Client for the local Windows bridge that owns the official COM module."""

    provider = "yuanta"

    def __init__(
        self,
        *,
        bridge_url: str = DEFAULT_BRIDGE_URL,
        bridge_token: str = "",
        mode: BrokerMode = BrokerMode.DISABLED,
        timeout: float = 15.0,
        transport=None,
    ) -> None:
        self.bridge_url = bridge_url.rstrip("/")
        self.bridge_token = bridge_token.strip()
        self.mode = mode
        self.timeout = timeout
        self.transport = transport or RequestsTransport()
        self._validate_bridge_url()

    @classmethod
    def from_env(cls, *, transport=None) -> "YuantaSecuritiesClient":
        return cls(
            bridge_url=os.getenv("YUANTA_BRIDGE_URL", DEFAULT_BRIDGE_URL),
            bridge_token=os.getenv("YUANTA_BRIDGE_TOKEN", ""),
            mode=BrokerMode.parse(os.getenv("YUANTA_SECURITIES_MODE")),
            timeout=float(os.getenv("YUANTA_BRIDGE_TIMEOUT_SECONDS", "15")),
            transport=transport,
        )

    def _validate_bridge_url(self) -> None:
        parsed = urlparse(self.bridge_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise BrokerConfigError(
                "YUANTA_BRIDGE_URL must use http://127.0.0.1. For another Windows host, "
                "use an SSH port forward to 127.0.0.1 instead of exposing the bridge."
            )

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "mode": self.mode.value,
            "configured": len(self.bridge_token) >= 32,
            "bridgeUrl": self.bridge_url,
            "liveOrdersEnabled": False,
            "capabilities": ["prices", "holdings", "order-preview"],
            "architecture": "windows-loopback-com-bridge",
        }

    def _require_read(self) -> None:
        if not self.mode.allows_read:
            raise BrokerConfigError(
                "Yuanta Securities is disabled. Set YUANTA_SECURITIES_MODE=read_only "
                "or paper to enable bridge reads."
            )
        if len(self.bridge_token) < 32:
            raise BrokerConfigError(
                "YUANTA_BRIDGE_TOKEN must contain at least 32 characters."
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_read()
        response = self.transport.request(
            method,
            f"{self.bridge_url}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.bridge_token}",
            },
            json_body=body,
            timeout=self.timeout,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            payload = response_json(response)
        except ValueError as exc:
            raise BrokerApiError(
                str(exc), code="invalid-bridge-response", status_code=status or None
            ) from exc
        if 200 <= status < 300:
            return payload
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        raise BrokerApiError(
            str(error.get("message") or "Yuanta bridge request failed."),
            code=str(error.get("code") or "yuanta-bridge-error"),
            status_code=status or None,
            request_id=str(error.get("requestId") or "") or None,
        )

    def get_bridge_status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/status")

    def get_prices(self, symbols: list[str]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/v1/prices",
            body={"symbols": validate_symbols(symbols, maximum=20)},
        )

    def get_holdings(self) -> dict[str, Any]:
        return self._request("GET", "/v1/holdings")

    def prepare_order(self, order: OrderRequest) -> dict[str, Any]:
        if order.order_amount:
            raise ValueError("Yuanta order previews require a whole-share quantity.")
        return {
            "ok": True,
            "provider": self.provider,
            "mode": self.mode.value,
            "willSubmit": False,
            "order": order.as_safe_dict(),
            "warning": (
                "Preview only. Yuanta live orders are intentionally not exposed by "
                "the NoSlip bridge until the Windows COM path is paper-tested and "
                "independently reviewed."
            ),
        }
