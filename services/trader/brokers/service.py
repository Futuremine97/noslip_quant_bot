from __future__ import annotations

from typing import Any

from .base import BrokerConfigError, OrderRequest
from .toss_securities import TossSecuritiesClient
from .yuanta_securities import YuantaSecuritiesClient
from .kb_securities import KBSecuritiesClient
from .kis_securities import KISSecuritiesClient
from .kiwoom_securities import KiwoomSecuritiesClient
from .shinhan_securities import ShinhanSecuritiesClient
from .nh_securities import NHSecuritiesClient
from .hana_securities import HanaSecuritiesClient


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
        "kis": "kis",
        "kis-securities": "kis",
        "한국투자": "kis",
        "한국투자증권": "kis",
        "한투": "kis",
        "kiwoom": "kiwoom",
        "kiwoom-securities": "kiwoom",
        "키움": "kiwoom",
        "키움증권": "kiwoom",
        "shinhan": "shinhan",
        "shinhan-securities": "shinhan",
        "신한": "shinhan",
        "신한투자증권": "shinhan",
        "신한금융투자": "shinhan",
        "nh": "nh",
        "nh-securities": "nh",
        "nh투자": "nh",
        "nh투자증권": "nh",
        "나무": "nh",
        "hana": "hana",
        "hana-securities": "hana",
        "하나": "hana",
        "하나증권": "hana",
        "하나금융투자": "hana",
    }
    resolved = aliases.get(normalized)
    if not resolved:
        allowed = ["toss", "yuanta", "kb", "kis", "kiwoom", "shinhan", "nh", "hana"]
        raise BrokerConfigError(f"provider must be one of: {', '.join(allowed)}")
    return resolved


def get_broker(provider: str, *, transport=None):
    resolved = normalize_provider(provider)
    if resolved == "toss":
        return TossSecuritiesClient.from_env(transport=transport)
    elif resolved == "kb":
        return KBSecuritiesClient.from_env(transport=transport)
    elif resolved == "kis":
        return KISSecuritiesClient.from_env(transport=transport)
    elif resolved == "kiwoom":
        return KiwoomSecuritiesClient.from_env(transport=transport)
    elif resolved == "shinhan":
        return ShinhanSecuritiesClient.from_env(transport=transport)
    elif resolved == "nh":
        return NHSecuritiesClient.from_env(transport=transport)
    elif resolved == "hana":
        return HanaSecuritiesClient.from_env(transport=transport)
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
            KISSecuritiesClient.from_env().status(),
            KiwoomSecuritiesClient.from_env().status(),
            ShinhanSecuritiesClient.from_env().status(),
            NHSecuritiesClient.from_env().status(),
            HanaSecuritiesClient.from_env().status(),
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
