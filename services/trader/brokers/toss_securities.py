from __future__ import annotations

import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .base import (
    BrokerApiError,
    BrokerConfigError,
    BrokerMode,
    OrderRequest,
    parse_bool,
    require_live_confirmation,
    validate_symbol,
    validate_symbols,
)
from .http import RequestsTransport, response_json


OFFICIAL_BASE_URL = "https://openapi.tossinvest.com"
ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass
class TossToken:
    value: str
    expires_at: float


class TossSecuritiesClient:
    provider = "toss"

    def __init__(
        self,
        *,
        client_id: str = "",
        client_secret: str = "",
        account_seq: str = "",
        base_url: str = OFFICIAL_BASE_URL,
        mode: BrokerMode = BrokerMode.DISABLED,
        allow_live_orders: bool = False,
        allow_custom_base_url: bool = False,
        timeout: float = 10.0,
        transport=None,
        clock=time.time,
    ) -> None:
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.account_seq = str(account_seq or "").strip()
        self.base_url = base_url.rstrip("/")
        self.mode = mode
        self.allow_live_orders = allow_live_orders
        self.timeout = timeout
        self.transport = transport or RequestsTransport()
        self.clock = clock
        self._token: TossToken | None = None
        self._token_lock = threading.Lock()
        self._validate_base_url(allow_custom_base_url)

    @classmethod
    def from_env(cls, *, transport=None) -> "TossSecuritiesClient":
        return cls(
            client_id=os.getenv("TOSS_SECURITIES_CLIENT_ID", ""),
            client_secret=os.getenv("TOSS_SECURITIES_CLIENT_SECRET", ""),
            account_seq=os.getenv("TOSS_SECURITIES_ACCOUNT_SEQ", ""),
            base_url=os.getenv("TOSS_SECURITIES_BASE_URL", OFFICIAL_BASE_URL),
            mode=BrokerMode.parse(os.getenv("TOSS_SECURITIES_MODE")),
            allow_live_orders=parse_bool(
                os.getenv("TOSS_SECURITIES_ALLOW_LIVE_ORDERS")
            ),
            allow_custom_base_url=parse_bool(
                os.getenv("TOSS_SECURITIES_ALLOW_CUSTOM_BASE_URL")
            ),
            timeout=float(os.getenv("TOSS_SECURITIES_TIMEOUT_SECONDS", "10")),
            transport=transport,
        )

    def _validate_base_url(self, allow_custom: bool) -> None:
        parsed = urlparse(self.base_url)
        if self.base_url == OFFICIAL_BASE_URL:
            return
        is_loopback = parsed.hostname == "127.0.0.1"
        if not allow_custom:
            raise BrokerConfigError(
                "Custom Toss Securities base URLs require "
                "TOSS_SECURITIES_ALLOW_CUSTOM_BASE_URL=true."
            )
        if parsed.scheme != "https" and not (parsed.scheme == "http" and is_loopback):
            raise BrokerConfigError(
                "Custom Toss Securities base URL must use HTTPS or 127.0.0.1 HTTP."
            )

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "mode": self.mode.value,
            "configured": bool(self.client_id and self.client_secret),
            "accountConfigured": bool(self.account_seq),
            "liveOrdersEnabled": bool(
                self.mode.allows_live_order and self.allow_live_orders
            ),
            "baseUrl": self.base_url,
            "capabilities": [
                "accounts",
                "prices",
                "holdings",
                "orders",
                "order-preview",
            ],
        }

    def _require_read(self) -> None:
        if not self.mode.allows_read:
            raise BrokerConfigError(
                "Toss Securities is disabled. Set TOSS_SECURITIES_MODE=read_only "
                "or paper to enable read operations."
            )
        if not self.client_id or not self.client_secret:
            raise BrokerConfigError(
                "TOSS_SECURITIES_CLIENT_ID and TOSS_SECURITIES_CLIENT_SECRET are required."
            )

    def _access_token(self) -> str:
        self._require_read()
        now = self.clock()
        if self._token and self._token.expires_at - 60 > now:
            return self._token.value
        with self._token_lock:
            now = self.clock()
            if self._token and self._token.expires_at - 60 > now:
                return self._token.value
            response = self.transport.request(
                "POST",
                f"{self.base_url}/oauth2/token",
                headers={"Accept": "application/json"},
                form={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=self.timeout,
            )
            payload = self._parse_response(response, oauth=True)
            token = str(payload.get("access_token", "")).strip()
            expires_in = int(payload.get("expires_in", 0))
            if not token or expires_in <= 0:
                raise BrokerApiError(
                    "Toss Securities returned an invalid OAuth token response.",
                    code="invalid-token-response",
                    status_code=getattr(response, "status_code", None),
                )
            self._token = TossToken(token, now + expires_in)
            return token

    def _headers(self, *, account: bool = False) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token()}",
        }
        if account:
            if not self.account_seq:
                raise BrokerConfigError(
                    "TOSS_SECURITIES_ACCOUNT_SEQ is required for account and order APIs."
                )
            headers["X-Tossinvest-Account"] = self.account_seq
        return headers

    def _parse_response(self, response, *, oauth: bool = False) -> dict[str, Any]:
        status = int(getattr(response, "status_code", 0) or 0)
        try:
            payload = response_json(response)
        except ValueError as exc:
            raise BrokerApiError(
                str(exc),
                code="invalid-response",
                status_code=status or None,
            ) from exc
        if 200 <= status < 300:
            return payload

        headers = getattr(response, "headers", {}) or {}
        if oauth:
            code = str(payload.get("error", "oauth-error"))
            message = str(
                payload.get("error_description")
                or "Toss Securities OAuth request failed."
            )
            request_id = headers.get("X-Request-Id")
        else:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            code = str(error.get("code") or "broker-api-error")
            message = str(error.get("message") or "Toss Securities API request failed.")
            request_id = str(error.get("requestId") or headers.get("X-Request-Id") or "")
        raise BrokerApiError(
            message,
            code=code,
            status_code=status or None,
            request_id=request_id or None,
            retry_after=headers.get("Retry-After"),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        account: bool = False,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.transport.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers(account=account),
            params=params,
            json_body=body,
            timeout=self.timeout,
        )
        return self._parse_response(response)

    def get_accounts(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/accounts")

    def get_prices(self, symbols: list[str]) -> dict[str, Any]:
        normalized = validate_symbols(symbols)
        return self._request(
            "GET",
            "/api/v1/prices",
            params={"symbols": ",".join(normalized)},
        )

    def get_holdings(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/holdings", account=True)

    def get_orders(
        self,
        *,
        status: str = "OPEN",
        symbol: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        normalized_status = status.strip().upper()
        if normalized_status not in {"OPEN", "CLOSED"}:
            raise ValueError("status must be OPEN or CLOSED.")
        if not 1 <= int(limit) <= 100:
            raise ValueError("limit must be between 1 and 100.")
        params: dict[str, Any] = {"status": normalized_status, "limit": int(limit)}
        if symbol:
            params["symbol"] = validate_symbol(symbol)
        return self._request(
            "GET", "/api/v1/orders", account=True, params=params
        )

    def prepare_order(self, order: OrderRequest) -> dict[str, Any]:
        return {
            "ok": True,
            "provider": self.provider,
            "mode": self.mode.value,
            "willSubmit": False,
            "order": order.as_safe_dict(),
            "warning": (
                "Preview only. No order was sent. Live submission requires mode=live, "
                "an explicit allow-live flag, and exact confirmation."
            ),
        }

    def submit_order(
        self, order: OrderRequest, *, confirmation: str = ""
    ) -> dict[str, Any]:
        require_live_confirmation(
            mode=self.mode,
            live_orders_enabled=self.allow_live_orders,
            confirmation=confirmation,
        )
        client_order_id = order.client_order_id or f"noslip-{uuid.uuid4().hex[:28]}"
        live_order = OrderRequest.create(
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=order.quantity,
            price=order.price,
            order_amount=order.order_amount,
            time_in_force=order.time_in_force,
            client_order_id=client_order_id,
            confirm_high_value_order=order.confirm_high_value_order,
        )
        return self._request(
            "POST",
            "/api/v1/orders",
            account=True,
            body=live_order.as_toss_payload(),
        )

    def cancel_order(self, order_id: str, *, confirmation: str = "") -> dict[str, Any]:
        require_live_confirmation(
            mode=self.mode,
            live_orders_enabled=self.allow_live_orders,
            confirmation=confirmation,
        )
        normalized = str(order_id or "").strip()
        if (
            not normalized
            or len(normalized) > 200
            or not ORDER_ID_PATTERN.fullmatch(normalized)
        ):
            raise ValueError("A valid order_id is required.")
        return self._request(
            "POST",
            f"/api/v1/orders/{normalized}/cancel",
            account=True,
            body={},
        )
