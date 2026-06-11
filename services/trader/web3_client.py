#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP client for NoSlip Credits and Base payment-intent APIs.

This client never accepts, stores, or transmits private keys or seed phrases.
It can prepare payment intents and ask the backend to verify/confirm them.
"""
from __future__ import annotations

import os
from typing import Any

import requests


FEATURES = {
    "personal_forecast",
    "zero_shot_forecast",
    "premium_whale_report",
    "premium_signal_feed",
    "strategy_tournament",
    "api_usage",
}


def _base_url() -> str:
    return (
        os.getenv("NOSLIP_WEB_APP_URL", "").strip()
        or os.getenv("NOSLIP_DASHBOARD_URL", "").strip()
        or "http://localhost:3000"
    ).rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = (
        os.getenv("NOSLIP_API_TOKEN", "").strip()
        or os.getenv("PREDICTION_API_TOKEN", "").strip()
    )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = requests.request(
        method,
        f"{_base_url()}{path}",
        params=params,
        json=payload,
        headers=_headers(),
        timeout=15,
    )
    try:
        data = response.json()
    except ValueError as error:
        raise RuntimeError(
            f"NoSlip Web3 API returned non-JSON HTTP {response.status_code}"
        ) from error
    if not response.ok:
        error_message = data.get("error") or f"HTTP {response.status_code}"
        if error_message == "INSUFFICIENT_CREDITS":
            error_message = (
                f"INSUFFICIENT_CREDITS: required={data.get('required')}, "
                f"balance={data.get('balance')}"
            )
        raise RuntimeError(str(error_message))
    return data


def get_credit_balance(user_id: str) -> dict[str, Any]:
    return _request(
        "GET",
        "/api/credits/balance",
        params={"userId": user_id},
    )


def estimate_feature_cost(feature: str) -> dict[str, Any]:
    if feature not in FEATURES:
        raise ValueError(f"Unknown premium feature: {feature}")
    return _request(
        "GET",
        "/api/credits/cost",
        params={"feature": feature},
    )


def create_credit_payment_intent(
    user_id: str,
    package_id: str = "starter",
    wallet_address: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "userId": user_id,
        "packageId": package_id,
    }
    if wallet_address.strip():
        payload["walletAddress"] = wallet_address.strip()
    return _request("POST", "/api/payments/create-intent", payload=payload)


def confirm_credit_payment(
    user_id: str,
    intent_id: str,
    tx_hash: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "userId": user_id,
        "intentId": intent_id,
    }
    if tx_hash.strip():
        payload["txHash"] = tx_hash.strip()
    return _request("POST", "/api/payments/confirm", payload=payload)


def check_premium_access(user_id: str, feature: str) -> dict[str, Any]:
    if feature not in FEATURES:
        raise ValueError(f"Unknown premium feature: {feature}")
    return _request(
        "GET",
        "/api/credits/access",
        params={"userId": user_id, "feature": feature},
    )


def consume_premium_access(user_id: str, feature: str) -> dict[str, Any]:
    if feature not in FEATURES:
        raise ValueError(f"Unknown premium feature: {feature}")
    return _request(
        "POST",
        "/api/credits/debit",
        payload={"userId": user_id, "feature": feature},
    )
