from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from services.trader.brokers.base import (
    BrokerConfigError,
    BrokerMode,
    BrokerSafetyError,
    OrderRequest,
)
from services.trader.brokers.toss_securities import TossSecuritiesClient
from services.trader.brokers.http import RequestsTransport
from services.trader.brokers.yuanta_securities import YuantaSecuritiesClient
from services.trader.brokers.yuanta_windows_driver import MockYuantaDriver


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeTransport:
    def __init__(self):
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if url.endswith("/oauth2/token"):
            return FakeResponse(
                200,
                {
                    "access_token": "test-access-token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        if url.endswith("/api/v1/prices"):
            return FakeResponse(
                200,
                {
                    "result": [
                        {
                            "symbol": "005930",
                            "lastPrice": "72000",
                            "currency": "KRW",
                        }
                    ]
                },
            )
        if url.endswith("/api/v1/holdings"):
            return FakeResponse(200, {"result": []})
        if url.endswith("/api/v1/orders"):
            return FakeResponse(
                200,
                {"result": {"orderId": "order-1", "clientOrderId": "test-order"}},
            )
        raise AssertionError(f"Unexpected request: {method} {url}")


class FakeYuantaTransport:
    def __init__(self):
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        if url.endswith("/v1/prices"):
            return FakeResponse(
                200,
                {
                    "ok": True,
                    "result": [
                        {
                            "symbol": "005930",
                            "lastPrice": "72000",
                            "currency": "KRW",
                        }
                    ],
                },
            )
        if url.endswith("/v1/holdings"):
            return FakeResponse(
                200, {"ok": True, "result": {"holdings": [], "summary": {}}}
            )
        raise AssertionError(f"Unexpected request: {method} {url}")


class OrderRequestTests(unittest.TestCase):
    def test_limit_order_requires_price(self):
        with self.assertRaises(ValueError):
            OrderRequest.create(
                symbol="005930",
                side="BUY",
                order_type="LIMIT",
                quantity=1,
            )

    def test_amount_order_is_market_only(self):
        with self.assertRaises(ValueError):
            OrderRequest.create(
                symbol="AAPL",
                side="BUY",
                order_type="LIMIT",
                order_amount="100",
                price="180",
            )


class TossSecuritiesTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.client = TossSecuritiesClient(
            client_id="client-id",
            client_secret="client-secret",
            account_seq="1",
            mode=BrokerMode.READ_ONLY,
            transport=self.transport,
            clock=lambda: 1000,
        )

    def test_token_is_cached_and_account_header_is_added(self):
        self.client.get_holdings()
        self.client.get_holdings()
        token_calls = [
            call for call in self.transport.requests if call[1].endswith("/oauth2/token")
        ]
        holding_calls = [
            call
            for call in self.transport.requests
            if call[1].endswith("/api/v1/holdings")
        ]
        self.assertEqual(len(token_calls), 1)
        self.assertEqual(len(holding_calls), 2)
        headers = holding_calls[0][2]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-access-token")
        self.assertEqual(headers["X-Tossinvest-Account"], "1")

    def test_prices_are_normalized(self):
        payload = self.client.get_prices(["005930", "005930"])
        self.assertEqual(payload["result"][0]["lastPrice"], "72000")
        price_call = self.transport.requests[-1]
        self.assertEqual(price_call[2]["params"]["symbols"], "005930")

    def test_live_order_is_blocked_by_default(self):
        order = OrderRequest.create(
            symbol="005930",
            side="BUY",
            order_type="LIMIT",
            quantity=1,
            price=70000,
        )
        with self.assertRaises(BrokerSafetyError):
            self.client.submit_order(order, confirmation="SUBMIT_LIVE_ORDER")

    def test_live_order_requires_and_uses_explicit_confirmation(self):
        client = TossSecuritiesClient(
            client_id="client-id",
            client_secret="client-secret",
            account_seq="1",
            mode=BrokerMode.LIVE,
            allow_live_orders=True,
            transport=self.transport,
            clock=lambda: 1000,
        )
        order = OrderRequest.create(
            symbol="005930",
            side="BUY",
            order_type="LIMIT",
            quantity=1,
            price=70000,
            client_order_id="test-order",
        )
        with self.assertRaises(BrokerSafetyError):
            client.submit_order(order)
        payload = client.submit_order(
            order, confirmation="SUBMIT_LIVE_ORDER"
        )
        self.assertEqual(payload["result"]["orderId"], "order-1")
        order_call = self.transport.requests[-1]
        self.assertEqual(order_call[2]["json_body"]["clientOrderId"], "test-order")


class YuantaSecuritiesTests(unittest.TestCase):
    def test_bridge_must_use_loopback(self):
        for bridge_url in (
            "http://192.0.2.10:8765",
            "http://localhost:8765",
            "https://127.0.0.1:8765",
        ):
            with self.subTest(bridge_url=bridge_url):
                with self.assertRaises(BrokerConfigError):
                    YuantaSecuritiesClient(
                        bridge_url=bridge_url,
                        bridge_token="x" * 32,
                        mode=BrokerMode.READ_ONLY,
                    )

    def test_bridge_adds_bearer_token_and_never_submits_order(self):
        transport = FakeYuantaTransport()
        client = YuantaSecuritiesClient(
            bridge_token="x" * 32,
            mode=BrokerMode.READ_ONLY,
            transport=transport,
        )
        payload = client.get_prices(["005930"])
        self.assertEqual(payload["result"][0]["lastPrice"], "72000")
        request = transport.requests[0]
        self.assertEqual(
            request[2]["headers"]["Authorization"], f"Bearer {'x' * 32}"
        )
        preview = client.prepare_order(
            OrderRequest.create(
                symbol="005930",
                side="SELL",
                order_type="MARKET",
                quantity=1,
            )
        )
        self.assertFalse(preview["willSubmit"])

    def test_mock_driver_does_not_claim_real_connection(self):
        with patch.dict(
            os.environ, {"YUANTA_MOCK_PRICES_JSON": '{"005930": "71000"}'}
        ):
            driver = MockYuantaDriver()
        self.assertFalse(driver.status()["connected"])
        self.assertEqual(driver.get_prices(["005930"])[0]["lastPrice"], "71000")


class RequestsTransportTests(unittest.TestCase):
    def test_redirects_are_disabled(self):
        class FakeSession:
            def __init__(self):
                self.kwargs = None

            def request(self, **kwargs):
                self.kwargs = kwargs
                return FakeResponse(200, {})

        session = FakeSession()
        RequestsTransport(session=session).request(
            "GET",
            "https://example.invalid",
        )
        self.assertIs(session.kwargs["allow_redirects"], False)


if __name__ == "__main__":
    unittest.main()
