from __future__ import annotations

from typing import Any

from .base import BrokerConfigError, OrderRequest
from .toss_securities import TossSecuritiesClient
from .yuanta_securities import YuantaSecuritiesClient
from .kb_securities import KBSecuritiesClient


def normalize_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower().replace("_", "-")
    aliases = {
        "toss": "toss",
        "toss-securities": "toss",
        "토스": "toss",
        "토스증권": "toss",
        "yuanta": "yuanta",
        "yuanta-securities": "yuanta",
        "유안타": "yuanta",
        "유안타증권": "yuanta",
        "kb": "kb",
        "kb-securities": "kb",
        "kb증권": "kb",
        "국민은행증권": "kb",
    }
    resolved = aliases.get(normalized)
    if not resolved:
        raise BrokerConfigError("provider must be 'toss', 'yuanta', or 'kb'.")
    return resolved


def get_broker(provider: str, *, transport=None):
    resolved = normalize_provider(provider)
    if resolved == "toss":
        return TossSecuritiesClient.from_env(transport=transport)
    elif resolved == "kb":
        return KBSecuritiesClient.from_env(transport=transport)
    return YuantaSecuritiesClient.from_env(transport=transport)


def broker_status(provider: str = "") -> dict[str, Any]:
    if provider:
        client = get_broker(provider)
        return {"ok": True, "broker": client.status()}
    return {
        "ok": True,
        "brokers": [
            TossSecuritiesClient.from_env().status(),
            YuantaSecuritiesClient.from_env().status(),
            KBSecuritiesClient.from_env().status(),
        ],
    }


def prepare_broker_order(
    *,
    provider: str,
    symbol: str,
    side: str,
    order_type: str,
    quantity: str | int | None = None,
    price: str | int | float | None = None,
    order_amount: str | int | float | None = None,
    time_in_force: str = "DAY",
) -> dict[str, Any]:
    order = OrderRequest.create(
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=price,
        order_amount=order_amount,
        time_in_force=time_in_force,
    )
    return get_broker(provider).prepare_order(order)
