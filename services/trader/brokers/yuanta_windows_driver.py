from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

from .base import BrokerConfigError, validate_symbols


class MockYuantaDriver:
    """Development driver. It never connects to a brokerage account."""

    def __init__(self) -> None:
        raw_prices = os.getenv("YUANTA_MOCK_PRICES_JSON", "{}")
        try:
            parsed = json.loads(raw_prices)
        except json.JSONDecodeError as exc:
            raise BrokerConfigError("YUANTA_MOCK_PRICES_JSON must be valid JSON.") from exc
        self.prices = parsed if isinstance(parsed, dict) else {}

    def status(self) -> dict[str, Any]:
        return {
            "driver": "mock",
            "ready": True,
            "connected": False,
            "environment": "mock",
        }

    def get_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        result = []
        for symbol in validate_symbols(symbols, maximum=20):
            value = self.prices.get(symbol)
            result.append(
                {
                    "symbol": symbol,
                    "lastPrice": str(value) if value is not None else None,
                    "currency": "KRW",
                    "source": "yuanta-mock",
                }
            )
        return result

    def get_holdings(self) -> dict[str, Any]:
        return {
            "account": "mock",
            "summary": {},
            "holdings": [],
            "source": "yuanta-mock",
        }


class _YuantaEvents:
    def _ensure_state(self) -> None:
        if not hasattr(self, "_login_event"):
            self._login_event = threading.Event()
            self._received_request_ids = set()
            self._event_lock = threading.Lock()
            self._last_event_args = {}

    def KLogin(self, *args) -> None:
        self._ensure_state()
        self._last_event_args["login"] = args
        self._login_event.set()

    def ReceiveData(self, *args) -> None:
        self._ensure_state()
        with self._event_lock:
            self._last_event_args["data"] = args
            for value in args:
                if isinstance(value, int):
                    self._received_request_ids.add(value)

    def ReceiveError(self, *args) -> None:
        self._ensure_state()
        self._last_event_args["error"] = args
        with self._event_lock:
            for value in args:
                if isinstance(value, int):
                    self._received_request_ids.add(value)

    def ReceiveSystemMessage(self, *args) -> None:
        self._ensure_state()
        self._last_event_args["system"] = args

    def ReceiveRealData(self, *args) -> None:
        self._ensure_state()
        self._last_event_args["real"] = args


class WindowsYuantaComDriver:
    """
    Thin wrapper around Yuanta's official COM component.

    The official module is Windows-only and asynchronous. Credentials remain in
    this loopback bridge process and are never accepted by the NoSlip web app,
    MCP tools, or Telegram commands.
    """

    PROG_ID = "YuantaAPICOM.YuantaAPI"
    RESULT_SUCCESS = 1000
    ERROR_MAX_CODE = 0

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise BrokerConfigError("Yuanta COM driver requires Windows.")
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise BrokerConfigError(
                "Install pywin32 in the Windows bridge environment."
            ) from exc

        self.pythoncom = pythoncom
        self.win32com = win32com.client
        self.server = os.getenv("YUANTA_COM_SERVER", "simul.tradar.api.com").strip()
        self.api_path = os.getenv("YUANTA_COM_API_PATH", "").strip()
        self.user_id = os.getenv("YUANTA_USER_ID", "").strip()
        self.user_password = os.getenv("YUANTA_USER_PASSWORD", "")
        self.cert_password = os.getenv("YUANTA_CERT_PASSWORD", "")
        self.account_number = os.getenv("YUANTA_ACCOUNT_NUMBER", "").strip()
        self.account_aid = os.getenv("YUANTA_ACCOUNT_AID", "").strip()
        self.timeout = float(os.getenv("YUANTA_COM_TIMEOUT_SECONDS", "15"))
        self._lock = threading.RLock()
        self._api = None
        self._connected = False

    def _require_credentials(self) -> None:
        if not self.user_id or not self.user_password:
            raise BrokerConfigError(
                "YUANTA_USER_ID and YUANTA_USER_PASSWORD are required on the "
                "Windows bridge host."
            )

    def _pump_until(self, predicate, *, description: str) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            self.pythoncom.PumpWaitingMessages()
            if predicate():
                return
            time.sleep(0.02)
        raise TimeoutError(f"Timed out waiting for Yuanta {description}.")

    def connect(self) -> None:
        with self._lock:
            if self._connected:
                return
            self._require_credentials()
            self.pythoncom.CoInitialize()
            api = self.win32com.DispatchWithEvents(self.PROG_ID, _YuantaEvents)
            api._ensure_state()
            initial = int(api.YOA_Initial(self.server, self.api_path))
            if initial != self.RESULT_SUCCESS:
                raise RuntimeError(
                    f"Yuanta COM initialization failed with code {initial}."
                )
            api.YOA_Login(self.user_id, self.user_password, self.cert_password)
            self._pump_until(api._login_event.is_set, description="login")
            if int(api.YOA_GetAccountCount()) < 0:
                raise RuntimeError("Yuanta COM login did not expose an account.")
            self._api = api
            self._connected = True

    def close(self) -> None:
        with self._lock:
            if self._api is not None:
                try:
                    self._api.YOA_UnInitial()
                finally:
                    self._api = None
                    self._connected = False
                    self.pythoncom.CoUninitialize()

    def status(self) -> dict[str, Any]:
        return {
            "driver": "yuanta-com",
            "ready": True,
            "connected": self._connected,
            "environment": (
                "simulation" if "simul" in self.server.lower() else "production"
            ),
            "server": self.server,
            "capabilities": ["prices", "holdings"],
        }

    def _request(
        self,
        tr_id: str,
        *,
        inputs: dict[tuple[str, str], str],
        outputs: dict[str, list[str]],
    ) -> dict[str, list[dict[str, str]]]:
        with self._lock:
            self.connect()
            api = self._api
            for (block, field), value in inputs.items():
                result = int(
                    api.YOA_SetTRFieldString(tr_id, block, field, str(value), 0)
                )
                if result != self.RESULT_SUCCESS:
                    raise RuntimeError(
                        f"Yuanta input validation failed for {tr_id}.{field}."
                    )
            req_id = int(api.YOA_Request(tr_id, False, -1))
            if req_id <= self.ERROR_MAX_CODE:
                message = str(api.YOA_GetErrorMessage(req_id))
                raise RuntimeError(
                    f"Yuanta request {tr_id} failed with code {req_id}: {message}"
                )
            self._pump_until(
                lambda: req_id in api._received_request_ids,
                description=f"TR {tr_id}",
            )
            result_payload: dict[str, list[dict[str, str]]] = {}
            try:
                for block, fields in outputs.items():
                    row_count = int(api.YOA_GetRowCount(tr_id, block))
                    if row_count <= 0:
                        row_count = 1
                    result_payload[block] = [
                        {
                            field: str(
                                api.YOA_GetTRFieldString(
                                    tr_id, block, field, row_index
                                )
                            ).strip()
                            for field in fields
                        }
                        for row_index in range(row_count)
                    ]
                return result_payload
            finally:
                api.YOA_ReleaseData(req_id)
                api._received_request_ids.discard(req_id)

    def get_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        result = []
        for symbol in validate_symbols(symbols, maximum=20):
            payload = self._request(
                "300001",
                inputs={
                    ("InBlock1", "jang"): "1",
                    ("InBlock1", "jongcode"): symbol,
                    ("InBlock1", "outflag"): "N",
                    ("InBlock1", "tsflag"): "0",
                },
                outputs={
                    "OutBlock1": [
                        "jongname",
                        "curjuka",
                        "debi",
                        "debirate",
                        "volume",
                    ]
                },
            )
            quote = payload["OutBlock1"][0]
            result.append(
                {
                    "symbol": symbol,
                    "name": quote["jongname"],
                    "lastPrice": quote["curjuka"],
                    "change": quote["debi"],
                    "changeRate": quote["debirate"],
                    "volume": quote["volume"],
                    "currency": "KRW",
                    "source": "yuanta-com",
                }
            )
        return result

    def get_holdings(self) -> dict[str, Any]:
        if not self.account_number or not self.account_aid:
            raise BrokerConfigError(
                "YUANTA_ACCOUNT_NUMBER and YUANTA_ACCOUNT_AID are required for holdings."
            )
        payload = self._request(
            "202021",
            inputs={
                ("InBlock1", "kyejwa"): self.account_number,
                ("InBlock1", "acnt_aid"): self.account_aid,
                ("InBlock1", "jong_code"): "",
                ("InBlock1", "gubun"): "0",
                ("InBlock1", "sise_tp"): "0",
                ("InBlock1", "entry_fee"): "0",
                ("InBlock1", "eval_fee"): "0",
                ("InBlock1", "add_loan"): "1",
                ("InBlock1", "unlist_tp"): "0",
                ("InBlock1", "tot_qty_tp"): "0",
            },
            outputs={
                "OutBlock1": [
                    "dpo",
                    "tot_book_amt",
                    "tot_eval_amt",
                    "tot_eval_sb_pl",
                    "tot_sb_rt",
                ],
                "OutBlock2": [
                    "stk_name",
                    "jangbu_price",
                    "curju_price",
                    "cur_qty_cnt",
                    "medo_cnt",
                    "evalprofit_amt",
                    "profit_rate",
                ],
                "OutBlock4": [
                    "stk_code",
                    "stk_name",
                    "qty_cnt",
                    "tdypossible_cnt",
                    "jangbu_price",
                ],
            },
        )
        return {
            "account": _mask_account(self.account_number),
            "summary": payload["OutBlock1"][0],
            "holdings": payload.get("OutBlock2", []),
            "positions": payload.get("OutBlock4", []),
            "source": "yuanta-com",
        }


def _mask_account(account: str) -> str:
    normalized = account.strip()
    if len(normalized) <= 4:
        return "*" * len(normalized)
    return f"{normalized[:2]}{'*' * (len(normalized) - 4)}{normalized[-2:]}"


def create_yuanta_driver():
    mode = os.getenv("YUANTA_BRIDGE_DRIVER", "mock").strip().lower()
    if mode == "mock":
        return MockYuantaDriver()
    if mode == "com":
        return WindowsYuantaComDriver()
    raise BrokerConfigError("YUANTA_BRIDGE_DRIVER must be 'mock' or 'com'.")
