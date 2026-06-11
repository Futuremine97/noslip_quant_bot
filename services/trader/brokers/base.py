from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9.\-]+$")
CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
LIVE_ORDER_CONFIRMATION = "SUBMIT_LIVE_ORDER"


class BrokerError(RuntimeError):
    """Base exception that is safe to display without credential details."""


class BrokerConfigError(BrokerError):
    pass


class BrokerSafetyError(BrokerError):
    pass


class BrokerApiError(BrokerError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "broker-api-error",
        status_code: int | None = None,
        request_id: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.request_id = request_id
        self.retry_after = retry_after

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.code,
            "message": str(self),
            "statusCode": self.status_code,
            "requestId": self.request_id,
            "retryAfter": self.retry_after,
        }


class BrokerMode(str, Enum):
    DISABLED = "disabled"
    READ_ONLY = "read_only"
    PAPER = "paper"
    LIVE = "live"

    @classmethod
    def parse(
        cls,
        raw: str | None,
        *,
        default: "BrokerMode | str | None" = None,
    ) -> "BrokerMode":
        value = (raw or "").strip().lower().replace("-", "_")
        if not value:
            return cls(default) if default is not None else cls.DISABLED
        try:
            return cls(value)
        except ValueError as exc:
            allowed = ", ".join(mode.value for mode in cls)
            raise BrokerConfigError(
                f"Invalid broker mode '{value}'. Expected one of: {allowed}."
            ) from exc

    @property
    def allows_read(self) -> bool:
        return self is not BrokerMode.DISABLED

    @property
    def allows_live_order(self) -> bool:
        return self is BrokerMode.LIVE


def parse_bool(raw: str | None, *, default: bool = False) -> bool:
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def validate_symbol(symbol: str) -> str:
    normalized = str(symbol or "").strip().upper()
    if not normalized or len(normalized) > 32 or not SYMBOL_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Invalid symbol. Use 1-32 letters, numbers, '.', or '-' characters."
        )
    return normalized


def validate_symbols(symbols: list[str], *, maximum: int = 200) -> list[str]:
    normalized = [validate_symbol(symbol) for symbol in symbols]
    unique = list(dict.fromkeys(normalized))
    if not unique:
        raise ValueError("At least one symbol is required.")
    if len(unique) > maximum:
        raise ValueError(f"At most {maximum} symbols may be requested.")
    return unique


def decimal_string(
    value: str | int | float | Decimal | None,
    *,
    field: str,
    integer_only: bool = False,
) -> str:
    raw = str(value if value is not None else "").strip()
    if not raw or "e" in raw.lower():
        raise ValueError(f"{field} must be a positive decimal string.")
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a positive decimal string.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be greater than zero.")
    if integer_only and parsed != parsed.to_integral_value():
        raise ValueError(f"{field} must be a positive integer.")
    return format(parsed, "f")


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    order_type: str
    quantity: str | None = None
    price: str | None = None
    order_amount: str | None = None
    time_in_force: str = "DAY"
    client_order_id: str | None = None
    confirm_high_value_order: bool = False

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str | int | None = None,
        price: str | int | float | Decimal | None = None,
        order_amount: str | int | float | Decimal | None = None,
        time_in_force: str = "DAY",
        client_order_id: str | None = None,
        confirm_high_value_order: bool = False,
    ) -> "OrderRequest":
        normalized_symbol = validate_symbol(symbol)
        normalized_side = str(side or "").strip().upper()
        normalized_order_type = str(order_type or "").strip().upper()
        normalized_tif = str(time_in_force or "DAY").strip().upper()

        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL.")
        if normalized_order_type not in {"LIMIT", "MARKET"}:
            raise ValueError("order_type must be LIMIT or MARKET.")
        if normalized_tif not in {"DAY", "CLS"}:
            raise ValueError("time_in_force must be DAY or CLS.")

        normalized_quantity = (
            decimal_string(quantity, field="quantity", integer_only=True)
            if quantity is not None and str(quantity).strip()
            else None
        )
        normalized_amount = (
            decimal_string(order_amount, field="order_amount")
            if order_amount is not None and str(order_amount).strip()
            else None
        )
        normalized_price = (
            decimal_string(price, field="price")
            if price is not None and str(price).strip()
            else None
        )

        if bool(normalized_quantity) == bool(normalized_amount):
            raise ValueError("Provide exactly one of quantity or order_amount.")
        if normalized_amount:
            if normalized_order_type != "MARKET":
                raise ValueError("Amount-based orders must use MARKET.")
            if normalized_tif != "DAY":
                raise ValueError("Amount-based orders must use DAY.")
        if normalized_order_type == "LIMIT" and not normalized_price:
            raise ValueError("LIMIT orders require price.")
        if normalized_order_type == "MARKET" and normalized_price:
            raise ValueError("MARKET orders must not include price.")
        if normalized_tif == "CLS" and normalized_order_type != "LIMIT":
            raise ValueError("CLS currently requires a LIMIT order.")

        normalized_client_id = str(client_order_id or "").strip() or None
        if normalized_client_id and (
            len(normalized_client_id) > 36
            or not CLIENT_ORDER_ID_PATTERN.fullmatch(normalized_client_id)
        ):
            raise ValueError(
                "client_order_id must be at most 36 letters, numbers, '-' or '_'."
            )

        return cls(
            symbol=normalized_symbol,
            side=normalized_side,
            order_type=normalized_order_type,
            quantity=normalized_quantity,
            price=normalized_price,
            order_amount=normalized_amount,
            time_in_force=normalized_tif,
            client_order_id=normalized_client_id,
            confirm_high_value_order=bool(confirm_high_value_order),
        )

    def as_toss_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "symbol": self.symbol,
            "side": self.side,
            "orderType": self.order_type,
            "confirmHighValueOrder": self.confirm_high_value_order,
        }
        if self.client_order_id:
            payload["clientOrderId"] = self.client_order_id
        if self.quantity:
            payload["quantity"] = self.quantity
            payload["timeInForce"] = self.time_in_force
        if self.price:
            payload["price"] = self.price
        if self.order_amount:
            payload["orderAmount"] = self.order_amount
        return payload

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "orderType": self.order_type,
            "quantity": self.quantity,
            "price": self.price,
            "orderAmount": self.order_amount,
            "timeInForce": self.time_in_force,
            "clientOrderId": self.client_order_id,
            "confirmHighValueOrder": self.confirm_high_value_order,
        }


def require_live_confirmation(
    *,
    mode: BrokerMode,
    live_orders_enabled: bool,
    confirmation: str,
) -> None:
    if not mode.allows_live_order:
        raise BrokerSafetyError(
            "Live order submission is disabled. Set the provider mode to 'live' only "
            "after paper testing and operational review."
        )
    if not live_orders_enabled:
        raise BrokerSafetyError(
            "Live order submission requires the provider-specific allow-live flag."
        )
    if confirmation != LIVE_ORDER_CONFIRMATION:
        raise BrokerSafetyError(
            f"Live order submission requires confirmation='{LIVE_ORDER_CONFIRMATION}'."
        )
