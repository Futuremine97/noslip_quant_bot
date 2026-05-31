import base64
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

from .config import SETTINGS


class JupiterSwapClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Missing JUPITER_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key})

    @staticmethod
    def _to_float(x: Any) -> Optional[float]:
        if x is None or x == "":
            return None
        try:
            return float(x)
        except Exception:
            return None

    def get_order(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        taker: Optional[str] = None,
        timeout: int = 20,
    ) -> Dict[str, Any]:
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount),
        }
        if taker is not None:
            params["taker"] = taker

        r = self.session.get(SETTINGS.order_url, params=params, timeout=timeout)
        r.raise_for_status()
        j = r.json()
        j["_fetched_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        return j

    def flatten_order(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        route_plan = raw.get("routePlan", []) or []
        dex_labels = []
        for hop in route_plan:
            swap_info = hop.get("swapInfo", {}) or {}
            label = swap_info.get("label")
            if label:
                dex_labels.append(str(label))

        return {
            "fetched_at": raw.get("_fetched_at"),
            "requestId": raw.get("requestId"),
            "quoteId": raw.get("quoteId"),
            "router": raw.get("router"),
            "mode": raw.get("mode"),
            "inputMint": raw.get("inputMint"),
            "outputMint": raw.get("outputMint"),
            "inAmount": self._to_float(raw.get("inAmount")),
            "outAmount": self._to_float(raw.get("outAmount")),
            "priceImpact": self._to_float(raw.get("priceImpact")),
            "priceImpactPct": self._to_float(raw.get("priceImpactPct")),
            "slippageBps": self._to_float(raw.get("slippageBps")),
            "totalTime": self._to_float(raw.get("totalTime")),
            "expireAt": raw.get("expireAt"),
            "route_len": len(route_plan),
            "dex_labels": "|".join(dex_labels),
            "has_transaction": bool(raw.get("transaction")),
            "_raw_order": raw,
        }

    def collect_candidate_orders(
        self,
        input_mint: str,
        output_mint: str,
        amount: int,
        taker: str,
        rounds: int,
        sleep_seconds: float,
    ) -> List[Dict[str, Any]]:
        candidates = []
        for i in range(rounds):
            try:
                raw = self.get_order(
                    input_mint=input_mint,
                    output_mint=output_mint,
                    amount=amount,
                    taker=taker,
                )
                row = self.flatten_order(raw)
                candidates.append(row)
                print(
                    f"[exec-order {i+1}/{rounds}] router={row['router']} mode={row['mode']} "
                    f"outAmount={row['outAmount']} priceImpact={row['priceImpact']} totalTime={row['totalTime']}"
                )
            except Exception as e:
                print(f"[exec-order {i+1}/{rounds}] error: {e}")
            if i < rounds - 1:
                import time

                time.sleep(sleep_seconds)
        return candidates

    def sign_order_transaction(self, order_response: Dict[str, Any], wallet: Keypair) -> str:
        tx_b64 = order_response.get("transaction")
        if not tx_b64:
            raise ValueError("Order response does not include transaction")

        tx_bytes = base64.b64decode(tx_b64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        required_signers = list(tx.message.account_keys[: tx.message.header.num_required_signatures])
        if wallet.pubkey() not in required_signers:
            raise ValueError(f"Wallet pubkey {wallet.pubkey()} is not in required signer set")

        signer_index = required_signers.index(wallet.pubkey())
        sigs = list(tx.signatures)
        sigs[signer_index] = wallet.sign_message(to_bytes_versioned(tx.message))
        signed_tx = VersionedTransaction.populate(tx.message, sigs)
        return base64.b64encode(bytes(signed_tx)).decode("utf-8")

    def execute_order(
        self,
        signed_transaction_b64: str,
        request_id: str,
        last_valid_block_height: Optional[int] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        payload = {"signedTransaction": signed_transaction_b64, "requestId": request_id}
        if last_valid_block_height is not None:
            payload["lastValidBlockHeight"] = last_valid_block_height
        r = self.session.post(SETTINGS.execute_url, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()


@dataclass
class JupiterQuotePoller:
    api_key: str
    input_mint: str
    output_mint: str
    input_decimals: int
    output_decimals: int
    amount_in_smallest_unit: int
    require_ultra: bool = True
    session: requests.Session = field(default_factory=requests.Session)

    def __post_init__(self):
        if not self.api_key:
            raise ValueError("Missing JUPITER_API_KEY")
        self.session.headers.update({"x-api-key": self.api_key})

    @staticmethod
    def _to_float(x: Any) -> Optional[float]:
        if x is None or x == "":
            return None
        try:
            return float(x)
        except Exception:
            return None

    def get_quote_only(self, timeout: int = 20) -> Dict[str, Any]:
        params = {
            "inputMint": self.input_mint,
            "outputMint": self.output_mint,
            "amount": str(self.amount_in_smallest_unit),
        }
        r = self.session.get(SETTINGS.order_url, params=params, timeout=timeout)
        r.raise_for_status()
        j = r.json()
        j["_fetched_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        return j

    def quote_to_snapshot_row(self, quote: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mode = quote.get("mode")
        if self.require_ultra and mode != "ultra":
            return None

        in_amt = float(quote.get("inAmount", 0) or 0)
        out_amt = float(quote.get("outAmount", 0) or 0)
        if in_amt <= 0 or out_amt <= 0:
            return None

        input_ui = in_amt / (10 ** self.input_decimals)
        output_ui = out_amt / (10 ** self.output_decimals)
        implied_price = output_ui / input_ui

        route_plan = quote.get("routePlan", []) or []
        dex_labels = []
        for hop in route_plan:
            swap_info = hop.get("swapInfo", {}) or {}
            if swap_info.get("label"):
                dex_labels.append(str(swap_info["label"]))

        ts = pd.to_datetime(quote["_fetched_at"], utc=True)
        return {
            "ds": ts,
            "open": implied_price,
            "high": implied_price,
            "low": implied_price,
            "close": implied_price,
            "inAmount": in_amt,
            "outAmount": out_amt,
            "priceImpact": self._to_float(quote.get("priceImpact")),
            "priceImpactPct": self._to_float(quote.get("priceImpactPct")),
            "slippageBps": self._to_float(quote.get("slippageBps")),
            "totalTime": self._to_float(quote.get("totalTime")),
            "router": quote.get("router"),
            "mode": mode,
            "requestId": quote.get("requestId"),
            "route_len": len(route_plan),
            "dex_labels": "|".join(dex_labels),
        }


class JupiterQuoteBuffer:
    def __init__(self, max_rows: int = 50000):
        self.max_rows = max_rows
        self.snapshots = pd.DataFrame()

    def append_many(self, rows: List[Dict[str, Any]]):
        if not rows:
            return
        df = pd.DataFrame(rows)
        self.snapshots = pd.concat([self.snapshots, df], ignore_index=True)
        self.snapshots["ds"] = pd.to_datetime(self.snapshots["ds"], utc=True, errors="coerce")
        self.snapshots = self.snapshots.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)
        if len(self.snapshots) > self.max_rows:
            self.snapshots = self.snapshots.iloc[-self.max_rows :].reset_index(drop=True)

    def seed_from_historical(self, raw_df: pd.DataFrame):
        base = raw_df[["ds", "open", "high", "low", "close"]].copy()
        self.snapshots = pd.concat([self.snapshots, base], ignore_index=True)
        self.snapshots["ds"] = pd.to_datetime(self.snapshots["ds"], utc=True, errors="coerce")
        self.snapshots = self.snapshots.dropna(subset=["ds"]).sort_values("ds").reset_index(drop=True)

    def to_ohlc(self, rule: str) -> pd.DataFrame:
        if self.snapshots.empty:
            return pd.DataFrame(columns=["ds", "open", "high", "low", "close"])
        df = self.snapshots.copy().set_index("ds").sort_index()
        bars = df["close"].resample(rule).ohlc().dropna().reset_index()
        return bars
