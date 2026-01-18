from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
import pandas as pd


@dataclass(frozen=True)
class ChartRequest:
    symbol: str
    benchmark: str
    window_days: int
    output_path: Path
    purchase_date: date | None = None


def generate_comparison_chart(
    series: pd.Series,
    benchmark_series: pd.Series,
    request: ChartRequest,
) -> Path:
    end_date = series.index[-1]
    requested_start_date = end_date - timedelta(days=request.window_days)

    earliest_available = max(series.index[0], benchmark_series.index[0])
    if request.purchase_date:
        purchase_dt = pd.to_datetime(request.purchase_date)
        if purchase_dt > earliest_available:
            earliest_available = purchase_dt

    start_date = max(requested_start_date, earliest_available)

    stock_slice = series.loc[start_date:]
    bench_slice = benchmark_series.loc[start_date:]
    if stock_slice.empty or bench_slice.empty:
        raise ValueError(f"Not enough data to chart {request.symbol}.")

    stock_norm = _normalize(stock_slice)
    bench_norm = _normalize(bench_slice)

    actual_days = (end_date - start_date).days
    if actual_days < request.window_days:
        if request.purchase_date and pd.to_datetime(request.purchase_date) >= start_date:
            window_label = "since listing"
        else:
            window_label = f"{actual_days}d"
    else:
        window_label = f"{request.window_days}d"

    plt.figure(figsize=(6, 3))
    plt.plot(stock_norm.index, stock_norm.values, label=request.symbol, linewidth=2)
    plt.plot(bench_norm.index, bench_norm.values, label=request.benchmark, linewidth=2, alpha=0.7)
    plt.title(f"{request.symbol} vs {request.benchmark} ({window_label})")
    plt.xlabel("")
    plt.ylabel("Index (100=Start)")
    plt.grid(alpha=0.2)
    plt.legend(loc="upper left", fontsize=8)

    ax = plt.gca()
    if actual_days <= 30:
        has_january = any(d.month == 1 for d in stock_norm.index)
        interval = max(1, actual_days // 6)
        locator = mdates.DayLocator(interval=interval)
        ax.xaxis.set_major_locator(locator)
        if has_january:
            jan_dates = [d for d in stock_norm.index if d.month == 1]
            first_jan_date_obj = min(jan_dates).date() if jan_dates else None
            start_date_obj = pd.Timestamp(start_date).date()
            end_date_obj = pd.Timestamp(end_date).date()
            year_shown_flag = [False]

            def format_date_with_year(x, pos=None):
                dt = mdates.num2date(x)
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                dt_date = dt.date()
                if first_jan_date_obj and not year_shown_flag[0] and dt.month == 1 and dt.day <= 15:
                    if start_date_obj <= dt_date <= end_date_obj:
                        year_shown_flag[0] = True
                        return dt.strftime("%m/%d/%y")
                return dt.strftime("%m/%d")

            ax.xaxis.set_major_formatter(FuncFormatter(format_date_with_year))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        plt.xticks(rotation=45, ha="right")
    elif actual_days <= 90:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        plt.xticks(rotation=45, ha="right")
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.xticks(rotation=45, ha="right")

    if request.purchase_date:
        purchase_dt = pd.to_datetime(request.purchase_date)
        if stock_norm.index.min() <= purchase_dt <= stock_norm.index.max():
            purchase_val = stock_norm.loc[:purchase_dt].iloc[-1]
            plt.axvline(purchase_dt, color="gray", linestyle="--", linewidth=1)
            plt.text(
                purchase_dt,
                purchase_val,
                "IPO",
                fontsize=8,
                color="gray",
                verticalalignment="bottom",
            )

    request.output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(request.output_path, dpi=150)
    plt.close()
    return request.output_path


def _normalize(series: pd.Series) -> pd.Series:
    base = float(series.iloc[0])
    if base == 0:
        return series * 0
    return (series / base) * 100
