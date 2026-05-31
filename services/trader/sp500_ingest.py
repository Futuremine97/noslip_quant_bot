#!/usr/bin/env python3

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import argparse
import json
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Tuple

import pandas as pd
import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
SP500_DATA_DIR = ROOT_DIR / "data" / "sp500"
CONSTITUENTS_PATH = SP500_DATA_DIR / "sp500_constituents.csv"
CLOSE_MATRIX_PATH = SP500_DATA_DIR / "sp500_close_daily.csv"
METADATA_PATH = SP500_DATA_DIR / "sp500_dataset_metadata.json"
WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
}


def expected_equity_session_date(reference_date: str | None = None) -> str:
    current = (
        date.fromisoformat(reference_date)
        if reference_date
        else datetime.today().date()
    )
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def yfinance_inclusive_end_date(end_date: str) -> str:
    resolved = date.fromisoformat(end_date)
    return (resolved + timedelta(days=1)).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch and cache S&P 500 raw price data for No Slip."
    )
    parser.add_argument(
        "--start-date",
        default="2000-01-01",
        help="Historical start date for yfinance downloads.",
    )
    parser.add_argument(
        "--end-date",
        default=datetime.today().strftime("%Y-%m-%d"),
        help="Historical end date for yfinance downloads.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(SP500_DATA_DIR),
        help="Directory where the cached S&P500 files should be written.",
    )
    return parser.parse_args()


def fetch_sp500_constituents() -> pd.DataFrame:
    response = requests.get(
        WIKI_URL,
        headers=REQUEST_HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    try:
        tables = pd.read_html(StringIO(response.text))
    except ImportError:
        tables = [_parse_sp500_table_with_bs4(response.text)]
    if not tables:
        raise ValueError("Could not parse any tables from the S&P500 Wikipedia page.")

    tickers = tables[0].copy()
    tickers["YahooSymbol"] = (
        tickers["Symbol"].astype(str).str.strip().str.replace(".", "-", regex=False)
    )
    return tickers


def _parse_sp500_table_with_bs4(html: str) -> pd.DataFrame:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise ImportError(
            "Parsing the S&P500 Wikipedia table requires either pandas HTML extras "
            "(like lxml) or beautifulsoup4."
        ) from exc

    soup = BeautifulSoup(html, "html.parser")
    table = None
    for candidate in soup.find_all("table"):
        header_cells = [th.get_text(" ", strip=True) for th in candidate.find_all("th")]
        if "Symbol" in header_cells and "Security" in header_cells:
            table = candidate
            break

    if table is None:
        raise ValueError("Could not find the S&P500 constituents table in Wikipedia HTML.")

    rows = table.find_all("tr")
    if not rows:
        raise ValueError("The S&P500 constituents table did not contain any rows.")

    headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["th", "td"])]
    parsed_rows = []
    for row in rows[1:]:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if len(values) < len(headers):
            values.extend([""] * (len(headers) - len(values)))
        parsed_rows.append(values[: len(headers)])

    if not parsed_rows:
        raise ValueError("The S&P500 constituents table was empty after parsing.")

    return pd.DataFrame(parsed_rows, columns=headers)


def download_sp500_close_matrix(
    constituents: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ImportError(
            "yfinance is required for S&P500 ingestion. "
            "Install services/trader/requirements.txt first."
        ) from exc

    symbols = constituents["YahooSymbol"].dropna().astype(str).tolist()
    if not symbols:
        raise ValueError("No S&P500 symbols were available for download.")

    data = yf.download(
        symbols,
        start=start_date,
        end=yfinance_inclusive_end_date(end_date),
        auto_adjust=True,
        progress=False,
    )

    if data.empty:
        raise ValueError("yfinance returned an empty dataset for the S&P500 download.")

    close_df = data["Close"] if "Close" in data else data
    if isinstance(close_df, pd.Series):
        close_df = close_df.to_frame(name=symbols[0])

    close_df = close_df.copy()
    close_df.index = pd.to_datetime(close_df.index, errors="coerce")
    close_df = close_df[~close_df.index.isna()]
    close_df.index.name = "ds"
    close_df = close_df.reset_index()
    close_df.columns = [
        "ds" if str(column) == "ds" else str(column).upper()
        for column in close_df.columns
    ]
    return close_df


def build_metadata(
    constituents: pd.DataFrame,
    close_matrix: pd.DataFrame,
    *,
    start_date: str,
    end_date: str,
) -> dict:
    symbol_columns = [column for column in close_matrix.columns if column != "ds"]
    return {
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "source": {
            "constituents": WIKI_URL,
            "prices": "yfinance",
        },
        "startDate": start_date,
        "endDate": end_date,
        "constituentCount": int(len(constituents)),
        "downloadedSymbolCount": int(len(symbol_columns)),
        "rowCount": int(len(close_matrix)),
        "firstTimestamp": (
            str(close_matrix["ds"].iloc[0]) if not close_matrix.empty else None
        ),
        "lastTimestamp": (
            str(close_matrix["ds"].iloc[-1]) if not close_matrix.empty else None
        ),
    }


def save_sp500_dataset(
    constituents: pd.DataFrame,
    close_matrix: pd.DataFrame,
    *,
    output_dir: Path,
    start_date: str,
    end_date: str,
) -> Tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    constituents_path = output_dir / CONSTITUENTS_PATH.name
    close_matrix_path = output_dir / CLOSE_MATRIX_PATH.name
    metadata_path = output_dir / METADATA_PATH.name

    constituents.to_csv(constituents_path, index=False)
    close_matrix.to_csv(close_matrix_path, index=False)
    metadata = build_metadata(
        constituents,
        close_matrix,
        start_date=start_date,
        end_date=end_date,
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return constituents_path, close_matrix_path, metadata_path


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()

    constituents = fetch_sp500_constituents()
    close_matrix = download_sp500_close_matrix(
        constituents,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    constituents_path, close_path, metadata_path = save_sp500_dataset(
        constituents,
        close_matrix,
        output_dir=output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    print(
        json.dumps(
            {
                "ok": True,
                "constituentsPath": str(constituents_path),
                "closeMatrixPath": str(close_path),
                "metadataPath": str(metadata_path),
                "constituentCount": int(len(constituents)),
                "rowCount": int(len(close_matrix)),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
