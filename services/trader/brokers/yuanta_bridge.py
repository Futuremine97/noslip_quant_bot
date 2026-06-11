#!/usr/bin/env python3
from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

from .base import BrokerConfigError, validate_symbols
from .yuanta_windows_driver import create_yuanta_driver


MAX_BODY_BYTES = 64 * 1024


class YuantaBridgeServer(HTTPServer):
    def __init__(self, address, handler, *, driver, token: str):
        super().__init__(address, handler)
        self.driver = driver
        self.token = token


class YuantaBridgeHandler(BaseHTTPRequestHandler):
    server_version = "NoSlipYuantaBridge/1.0"

    def log_message(self, format: str, *args) -> None:
        # Do not log request bodies, Authorization headers, or account values.
        super().log_message(format, *args)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.token}"
        provided = self.headers.get("Authorization", "")
        return hmac.compare_digest(provided, expected)

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid Content-Length.") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("Request body must be between 1 and 65536 bytes.")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object.")
        return payload

    def _dispatch(self) -> None:
        if not self._authorized():
            self._json(
                401,
                {"ok": False, "error": {"code": "unauthorized", "message": "Unauthorized."}},
            )
            return
        path = urlparse(self.path).path
        if self.command == "GET" and path == "/v1/status":
            self._json(200, {"ok": True, "result": self.server.driver.status()})
            return
        if self.command == "GET" and path == "/v1/holdings":
            self._json(
                200, {"ok": True, "result": self.server.driver.get_holdings()}
            )
            return
        if self.command == "POST" and path == "/v1/prices":
            payload = self._body()
            symbols = payload.get("symbols")
            if not isinstance(symbols, list):
                raise ValueError("symbols must be an array.")
            result = self.server.driver.get_prices(
                validate_symbols(symbols, maximum=20)
            )
            self._json(200, {"ok": True, "result": result})
            return
        self._json(
            404,
            {"ok": False, "error": {"code": "not-found", "message": "Not found."}},
        )

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def _handle(self) -> None:
        try:
            self._dispatch()
        except (BrokerConfigError, ValueError) as exc:
            self._json(
                400,
                {
                    "ok": False,
                    "error": {"code": "invalid-request", "message": str(exc)},
                },
            )
        except TimeoutError as exc:
            self._json(
                504,
                {"ok": False, "error": {"code": "timeout", "message": str(exc)}},
            )
        except Exception:
            self._json(
                500,
                {
                    "ok": False,
                    "error": {
                        "code": "bridge-error",
                        "message": "Yuanta bridge operation failed.",
                    },
                },
            )


def main() -> None:
    host = os.getenv("YUANTA_BRIDGE_HOST", "127.0.0.1").strip()
    port = int(os.getenv("YUANTA_BRIDGE_PORT", "8765"))
    token = os.getenv("YUANTA_BRIDGE_TOKEN", "").strip()
    if host != "127.0.0.1":
        raise BrokerConfigError(
            "The Yuanta bridge must bind to 127.0.0.1. Use SSH port forwarding."
        )
    if len(token) < 32:
        raise BrokerConfigError(
            "YUANTA_BRIDGE_TOKEN must contain at least 32 characters."
        )
    server = YuantaBridgeServer(
        (host, port),
        YuantaBridgeHandler,
        driver=create_yuanta_driver(),
        token=token,
    )
    print(f"NoSlip Yuanta bridge listening on {host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        close = getattr(server.driver, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    main()
