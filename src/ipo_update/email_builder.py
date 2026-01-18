from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re

from .thesis import ThesisSummary, _markdown_to_html


@dataclass(frozen=True)
class ChartAsset:
    symbol: str
    window_label: str
    file_path: Path
    content_id: str


@dataclass(frozen=True)
class RecentIpoRow:
    name: str
    ticker: str | None
    ipo_date: date | None
    ipo_price: float | None
    perf_since_ipo: float | None
    return_1w: float | None
    return_1m: float | None
    recommendation: str


@dataclass(frozen=True)
class UpcomingIpoRow:
    name: str
    ticker: str | None
    indicative_price: float | None
    price_confidence: str | None
    expected_date: str | None
    date_status: str | None
    business_summary: str | None
    recommendation: str


def build_email_html(
    recent_rows: list[RecentIpoRow],
    upcoming_rows: list[UpcomingIpoRow],
    recent_summaries: dict[str, ThesisSummary],
    upcoming_summaries: dict[str, ThesisSummary],
    charts: list[ChartAsset],
) -> str:
    chart_lookup = _group_charts(charts)

    recent_table = _render_recent_table(recent_rows)
    upcoming_table = _render_upcoming_table(upcoming_rows)

    recent_blocks = []
    for row in recent_rows:
        key = row.ticker or row.name
        summary = recent_summaries.get(key)
        summary_text = summary.summary if summary else "No summary available."
        summary_html = _markdown_to_html(summary_text)
        charts_html = _render_charts(chart_lookup.get(key, []))
        header = f"{row.name} ({row.ticker})" if row.ticker else row.name
        recent_blocks.append(
            f"""
            <div class="card">
              <h3>{header}</h3>
              {charts_html}
              <div class="thesis-content">{summary_html}</div>
            </div>
            """
        )

    upcoming_blocks = []
    for row in upcoming_rows:
        key = row.ticker or row.name
        summary = upcoming_summaries.get(key)
        summary_text = summary.summary if summary else "No summary available."
        summary_html = _markdown_to_html(summary_text)
        header = f"{row.name} ({row.ticker})" if row.ticker else row.name
        upcoming_blocks.append(
            f"""
            <div class="card">
              <h3>{header}</h3>
              <div class="thesis-content">{summary_html}</div>
            </div>
            """
        )

    return f"""
    <html>
      <head>
        <style>
          body {{
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background: #ffffff;
            color: #111111;
          }}
          h1 {{
            font-size: 20px;
            margin-bottom: 10px;
          }}
          h2 {{
            font-size: 16px;
            margin-top: 24px;
          }}
          table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
          }}
          th, td {{
            padding: 8px;
            text-align: left;
            border-bottom: 1px solid #eeeeee;
            font-size: 12px;
          }}
          .header-row {{
            background-color: #f5f5f5;
            font-weight: bold;
          }}
          .card {{
            border: 1px solid #eeeeee;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 16px;
          }}
          .charts {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
          }}
          .chart {{
            flex: 1 1 200px;
          }}
          .thesis-content {{
            line-height: 1.6;
            margin-top: 12px;
          }}
          .thesis-content p {{
            margin: 8px 0;
          }}
          .thesis-content strong {{
            font-weight: bold;
            color: #222222;
          }}
          .thesis-content em {{
            font-style: italic;
          }}
        </style>
      </head>
      <body>
        <h1>IPO Weekly Update</h1>
        <h2>Recent IPOs</h2>
        {recent_table}
        {"".join(recent_blocks)}
        <h2>Upcoming IPOs</h2>
        {upcoming_table}
        {"".join(upcoming_blocks)}
      </body>
    </html>
    """


def _render_recent_table(rows: list[RecentIpoRow]) -> str:
    body = "\n".join(_render_recent_row(row) for row in rows)
    return f"""
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Name</th>
          <th>IPO Date</th>
          <th>IPO Price</th>
          <th>Since IPO</th>
          <th>1w</th>
          <th>1m</th>
          <th>Recommendation</th>
        </tr>
      </thead>
      <tbody>
        {body}
      </tbody>
    </table>
    """


def _render_recent_row(row: RecentIpoRow) -> str:
    return f"""
    <tr>
      <td>{row.ticker or "—"}</td>
      <td>{row.name}</td>
      <td>{row.ipo_date.strftime("%m/%d/%Y") if row.ipo_date else "—"}</td>
      <td>{_format_currency(row.ipo_price)}</td>
      <td>{_format_pct(row.perf_since_ipo)}</td>
      <td>{_format_pct(row.return_1w)}</td>
      <td>{_format_pct(row.return_1m)}</td>
      <td>{row.recommendation}</td>
    </tr>
    """


def _render_upcoming_table(rows: list[UpcomingIpoRow]) -> str:
    body = "\n".join(_render_upcoming_row(row) for row in rows)
    return f"""
    <table>
      <thead>
        <tr>
          <th>Ticker</th>
          <th>Name</th>
          <th>Indicative Price</th>
          <th>Price Confidence</th>
          <th>IPO Date</th>
          <th>Date Status</th>
          <th>Business</th>
          <th>Recommendation</th>
        </tr>
      </thead>
      <tbody>
        {body}
      </tbody>
    </table>
    """


def _render_upcoming_row(row: UpcomingIpoRow) -> str:
    return f"""
    <tr>
      <td>{row.ticker or "—"}</td>
      <td>{row.name}</td>
      <td>{_format_currency(row.indicative_price)}</td>
      <td>{row.price_confidence or "—"}</td>
      <td>{row.expected_date or "—"}</td>
      <td>{row.date_status or "—"}</td>
      <td>{row.business_summary or "—"}</td>
      <td>{row.recommendation}</td>
    </tr>
    """


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2%}"


def _format_currency(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def extract_recommendation(summary: str) -> str:
    if not summary:
        return "—"
    patterns = [
        r"Decision:\s*(STRONG BUY|BUY|PASS)",
        r"Recommendation:\s*(STRONG BUY|BUY|PASS)",
        r"Action:\s*(STRONG BUY|BUY|PASS)",
    ]
    for pattern in patterns:
        match = re.search(pattern, summary, re.IGNORECASE)
        if match:
            return match.group(1).upper()
    match = re.search(r"\b(STRONG BUY|BUY|PASS)\b", summary, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return "—"


def _group_charts(charts: list[ChartAsset]) -> dict[str, list[ChartAsset]]:
    grouped: dict[str, list[ChartAsset]] = {}
    for chart in charts:
        grouped.setdefault(chart.symbol, []).append(chart)
    return grouped


def _render_charts(charts: list[ChartAsset]) -> str:
    if not charts:
        return ""
    blocks = "\n".join(
        f"""
        <div class="chart">
          <img src="cid:{chart.content_id}" alt="{chart.symbol} {chart.window_label}" width="100%" />
          <div>{chart.window_label}</div>
        </div>
        """
        for chart in charts
    )
    return f'<div class="charts">{blocks}</div>'
