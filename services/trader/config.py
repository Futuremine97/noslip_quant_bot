import os
from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class Settings:
    swap_base_url: str = os.getenv("SWAP_BASE_URL", "https://api.jup.ag/swap/v2")
    order_url: str = f"{os.getenv('SWAP_BASE_URL', 'https://api.jup.ag/swap/v2')}/order"
    execute_url: str = f"{os.getenv('SWAP_BASE_URL', 'https://api.jup.ag/swap/v2')}/execute"

    jupiter_api_key: str = os.getenv("JUPITER_API_KEY", "")
    solana_private_key_b58: str = os.getenv("SOLANA_PRIVATE_KEY_B58", "")
    price_history_csv: str = os.getenv("PRICE_HISTORY_CSV", "data/historical/historical_price.csv")

    execute_trades: bool = os.getenv("EXECUTE_TRADES", "false").lower() == "true"
    wait_for_target: bool = os.getenv("WAIT_FOR_TARGET", "false").lower() == "true"

    input_mint_for_price: str = os.getenv(
        "INPUT_MINT_FOR_PRICE", "So11111111111111111111111111111111111111112"
    )
    output_mint_for_price: str = os.getenv(
        "OUTPUT_MINT_FOR_PRICE", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    )
    input_decimals: int = int(os.getenv("INPUT_DECIMALS", "9"))
    output_decimals: int = int(os.getenv("OUTPUT_DECIMALS", "6"))

    quote_amount_in_smallest_unit: int = int(
        os.getenv("QUOTE_AMOUNT_IN_SMALLEST_UNIT", "100000000")
    )
    buy_amount_usdc: int = int(os.getenv("BUY_AMOUNT_USDC", "10000000"))
    sell_amount_sol: int = int(os.getenv("SELL_AMOUNT_SOL", "100000000"))

    poll_every_seconds: float = float(os.getenv("POLL_EVERY_SECONDS", "5.0"))
    quote_burst_count: int = int(os.getenv("QUOTE_BURST_COUNT", "3"))
    quote_burst_sleep_seconds: float = float(os.getenv("QUOTE_BURST_SLEEP_SECONDS", "1.0"))
    max_iterations: int = int(os.getenv("MAX_ITERATIONS", "10"))
    target_lead_seconds: int = int(os.getenv("TARGET_LEAD_SECONDS", "60"))

    require_ultra_for_training: bool = os.getenv(
        "REQUIRE_ULTRA_FOR_TRAINING", "true"
    ).lower() == "true"

    cadence_rules: Tuple[str, ...] = ("10min", "5min", "1min")
    cadence_weights: Dict[str, float] = field(
        default_factory=lambda: {"10min": 0.45, "5min": 0.35, "1min": 0.20}
    )
    horizon_steps: Dict[str, int] = field(
        default_factory=lambda: {
            "10min": int(os.getenv("HORIZON_STEPS_10MIN", "6")),
            "5min": int(os.getenv("HORIZON_STEPS_5MIN", "12")),
            "1min": int(os.getenv("HORIZON_STEPS_1MIN", "30")),
        }
    )

    buy_threshold: float = float(os.getenv("BUY_THRESHOLD", "0.003"))
    sell_threshold: float = float(os.getenv("SELL_THRESHOLD", "-0.003"))
    max_uncertainty_ratio: float = float(os.getenv("MAX_UNCERTAINTY_RATIO", "0.03"))

    @property
    def has_api_key(self) -> bool:
        return bool(self.jupiter_api_key)

    @property
    def has_private_key(self) -> bool:
        return bool(self.solana_private_key_b58)


SETTINGS = Settings()
