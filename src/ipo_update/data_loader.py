from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterable
import json
import time

import pandas as pd
import requests

from .logger import get_logger


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def read_json(path: Path) -> dict | None:
    logger = get_logger(__name__)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        logger.info(f"Loaded cached JSON from {path}")
        return payload
    except json.JSONDecodeError:
        logger.error(f"Failed to parse cached JSON: {path}")
        return None


def write_json(path: Path, payload: dict) -> None:
    logger = get_logger(__name__)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(f"Wrote JSON cache to {path}")


def fetch_daily_adjusted(
    symbol: str, api_key: str, session: requests.Session | None = None
) -> pd.Series:
    logger = get_logger(__name__)
    session = session or requests.Session()
    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": symbol,
        "outputsize": "full",
        "apikey": api_key,
    }
    try:
        logger.debug(f"AlphaVantage API call: fetching daily adjusted prices for {symbol}")
        response = session.get("https://www.alphavantage.co/query", params=params, timeout=30)
        logger.debug(f"AlphaVantage API response: {symbol} status={response.status_code}")
        response.raise_for_status()
        payload = response.json()
        series = payload.get("Time Series (Daily)")
        if not series:
            error_msg = payload.get("Note") or str(payload)[:100]
            logger.error(f"AlphaVantage API error for {symbol}: {error_msg}")
            raise ValueError(f"Alpha Vantage error for {symbol}: {error_msg}")
        df = pd.DataFrame.from_dict(series, orient="index")
        df.index = pd.to_datetime(df.index)
        df.sort_index(inplace=True)
        logger.info(f"AlphaVantage API success: {symbol} fetched {len(df)} data points")
        return df["5. adjusted close"].astype(float)
    except requests.RequestException as exc:
        logger.error(f"AlphaVantage API request failed for {symbol}: {type(exc).__name__} - {str(exc)[:100]}")
        raise


def fetch_daily_adjusted_batch(
    symbols: Iterable[str], api_key: str
) -> dict[str, pd.Series]:
    logger = get_logger(__name__)
    symbols_list = list(symbols)
    logger.info(f"AlphaVantage batch fetch: starting for {len(symbols_list)} symbols")
    session = requests.Session()
    data: dict[str, pd.Series] = {}
    last_call = 0.0
    min_interval = 0.2
    for i, symbol in enumerate(symbols_list, 1):
        elapsed = time.monotonic() - last_call
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        data[symbol] = fetch_daily_adjusted(symbol, api_key, session=session)
        last_call = time.monotonic()
        logger.info(f"AlphaVantage batch fetch: {symbol} ({i}/{len(symbols_list)})")
    logger.info(f"AlphaVantage batch fetch completed: {len(data)}/{len(symbols_list)} symbols successful")
    return data
