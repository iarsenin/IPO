from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .ipo_finder import RecentIpo
from .logger import get_logger


@dataclass(frozen=True)
class IpoPerformance:
    name: str
    ticker: str | None
    ipo_date: date | None
    ipo_price: float | None
    current_price: float | None
    perf_since_ipo: float | None
    return_1w: float | None
    return_1m: float | None


def _price_on_or_after(series: pd.Series, target: date) -> float | None:
    if series.empty:
        return None
    target_dt = pd.Timestamp(target)
    candidates = series.loc[series.index >= target_dt]
    if candidates.empty:
        return None
    return float(candidates.iloc[0])


def _price_return(series: pd.Series, days: int) -> float | None:
    if series.empty:
        return None
    end_date = series.index[-1]
    start_date = end_date - timedelta(days=days)
    prior = series.loc[:start_date]
    if prior.empty:
        return None
    start_price = float(prior.iloc[-1])
    end_price = float(series.iloc[-1])
    if start_price == 0:
        return None
    return (end_price - start_price) / start_price


def compute_ipo_performance(ipo: RecentIpo, series: pd.Series | None) -> IpoPerformance:
    logger = get_logger(__name__)
    current_price = float(series.iloc[-1]) if series is not None and not series.empty else None

    ipo_price = ipo.ipo_price
    if ipo_price is None and ipo.ipo_date and series is not None and not series.empty:
        # Fallback to first available trading price after IPO date.
        fallback = _price_on_or_after(series, ipo.ipo_date)
        ipo_price = fallback
        if fallback is not None:
            logger.info(f"{ipo.name}: IPO price missing, using first post-IPO price ${fallback:.2f}")

    perf_since_ipo = None
    if ipo_price and current_price is not None:
        perf_since_ipo = (current_price - ipo_price) / ipo_price

    return_1w = _price_return(series, days=7) if series is not None else None
    return_1m = _price_return(series, days=30) if series is not None else None

    return IpoPerformance(
        name=ipo.name,
        ticker=ipo.ticker,
        ipo_date=ipo.ipo_date,
        ipo_price=ipo_price,
        current_price=current_price,
        perf_since_ipo=perf_since_ipo,
        return_1w=return_1w,
        return_1m=return_1m,
    )
