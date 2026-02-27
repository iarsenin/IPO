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
    source_quality: str
    recommendation: str


@dataclass(frozen=True)
class UpcomingIpoRow:
    name: str
    ticker: str | None
    indicative_price: float | None
    price_confidence: str | None
    expected_date: str | None
    date_status: str | None
    date_note: str | None
    business_summary: str | None
    source_quality: str
    edgar_confirmed: bool
    edgar_note: str | None
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
    has_recent_single_source = any(row.source_quality == "single-source" for row in recent_rows)
    has_upcoming_edgar_missing = any(not row.edgar_confirmed for row in upcoming_rows)

    recent_blocks = []
    for row in recent_rows:
        key = row.ticker or row.name
        summary = recent_summaries.get(key)
        summary_text = summary.summary if summary else "No summary available."
        summary_html = _markdown_to_html(summary_text)
        charts_html = _render_charts(chart_lookup.get(key, []))
        header = f"{row.name} ({row.ticker})" if row.ticker else row.name
        recent_blocks.append(
            f"""<div class="card" style="border:1px solid #e0e0e0;border-radius:6px;padding:16px;margin-bottom:16px;background:#fafafa;">
<h3 style="font-size:15px;font-weight:600;margin:0 0 10px 0;color:#1a1a1a;">{header}</h3>
{charts_html}
<div class="thesis-content" style="line-height:1.6;color:#333;">{summary_html}</div>
</div>"""
        )

    upcoming_blocks = []
    for row in upcoming_rows:
        key = row.ticker or row.name
        summary = upcoming_summaries.get(key)
        summary_text = summary.summary if summary else "No summary available."
        summary_html = _markdown_to_html(summary_text)
        header = f"{row.name} ({row.ticker})" if row.ticker else row.name
        upcoming_blocks.append(
            f"""<div class="card" style="border:1px solid #e0e0e0;border-radius:6px;padding:16px;margin-bottom:16px;background:#fafafa;">
<h3 style="font-size:15px;font-weight:600;margin:0 0 10px 0;color:#1a1a1a;">{header}</h3>
<div class="thesis-content" style="line-height:1.6;color:#333;">{summary_html}</div>
</div>"""
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style type="text/css">
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
  margin: 0;
  padding: 16px;
  background: #ffffff;
  color: #1a1a1a;
  font-size: 14px;
  line-height: 1.5;
}}
h1 {{
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 16px 0;
  color: #111111;
}}
h2 {{
  font-size: 17px;
  font-weight: 600;
  margin: 28px 0 12px 0;
  color: #111111;
  border-bottom: 2px solid #e0e0e0;
  padding-bottom: 6px;
}}
h3 {{
  font-size: 15px;
  font-weight: 600;
  margin: 0 0 10px 0;
  color: #1a1a1a;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 20px;
  font-size: 12px;
}}
th {{
  padding: 10px 8px;
  text-align: left;
  background-color: #f5f5f5;
  font-weight: 600;
  border-bottom: 2px solid #ddd;
  color: #333;
}}
td {{
  padding: 8px;
  text-align: left;
  border-bottom: 1px solid #eee;
  vertical-align: top;
}}
tr:hover {{
  background-color: #fafafa;
}}
.card {{
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
  background: #fafafa;
}}
.chart-table {{
  width: 100%;
  margin-bottom: 12px;
}}
.chart-cell {{
  width: 50%;
  padding: 4px;
  vertical-align: top;
  text-align: center;
}}
.chart-cell img {{
  max-width: 100%;
  height: auto;
  border: 1px solid #e0e0e0;
  border-radius: 4px;
}}
.chart-label {{
  font-size: 11px;
  color: #666;
  margin-top: 4px;
}}
.thesis-content {{
  line-height: 1.6;
  color: #333;
}}
.thesis-content p {{
  margin: 0 0 12px 0;
}}
.thesis-content p:last-child {{
  margin-bottom: 0;
}}
.thesis-content strong {{
  font-weight: 600;
  color: #111;
}}
.thesis-content em {{
  font-style: italic;
}}
.thesis-content a {{
  color: #0066cc;
  text-decoration: none;
}}
.thesis-content a:hover {{
  text-decoration: underline;
}}
.footnote {{
  font-size: 11px;
  color: #666;
  margin-bottom: 12px;
  font-style: italic;
}}
.rec-strong-buy {{ color: #0a7c42; font-weight: 600; }}
.rec-buy {{ color: #1a73e8; font-weight: 600; }}
.rec-pass {{ color: #b91c1c; font-weight: 600; }}
</style>
</head>
<body>
<h1>IPO Weekly Update</h1>
<h2>Recent IPOs</h2>
{recent_table}
{_render_single_source_footnote(has_recent_single_source)}
{"".join(recent_blocks)}
<h2>Upcoming IPOs</h2>
{upcoming_table}
{_render_edgar_footnote(has_upcoming_edgar_missing)}
{"".join(upcoming_blocks)}
</body>
</html>"""


def _render_recent_table(rows: list[RecentIpoRow]) -> str:
    body = "\n".join(_render_recent_row(row) for row in rows)
    th_style = "padding:10px 8px;text-align:left;background-color:#f5f5f5;font-weight:600;border-bottom:2px solid #ddd;color:#333;font-size:12px;"
    return f"""<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:12px;">
<thead>
<tr>
<th style="{th_style}">Ticker</th>
<th style="{th_style}">Name</th>
<th style="{th_style}">IPO Date</th>
<th style="{th_style}">IPO Price</th>
<th style="{th_style}">Since IPO</th>
<th style="{th_style}">1w</th>
<th style="{th_style}">1m</th>
<th style="{th_style}">Rec</th>
</tr>
</thead>
<tbody>
{body}
</tbody>
</table>"""


def _render_recent_row(row: RecentIpoRow) -> str:
    return f"""<tr>
<td style="padding:8px;border-bottom:1px solid #eee;">{row.ticker or "—"}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_render_recent_name(row.name, row.source_quality)}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{row.ipo_date.strftime("%m/%d/%Y") if row.ipo_date else "—"}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_format_currency(row.ipo_price)}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_format_pct_colored(row.perf_since_ipo)}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_format_pct_colored(row.return_1w)}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_format_pct_colored(row.return_1m)}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_format_recommendation(row.recommendation)}</td>
</tr>"""


def _render_upcoming_table(rows: list[UpcomingIpoRow]) -> str:
    body = "\n".join(_render_upcoming_row(row) for row in rows)
    th_style = "padding:10px 8px;text-align:left;background-color:#f5f5f5;font-weight:600;border-bottom:2px solid #ddd;color:#333;font-size:11px;"
    return f"""<table cellpadding="0" cellspacing="0" border="0" style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:12px;">
<thead>
<tr>
<th style="{th_style}">Ticker</th>
<th style="{th_style}">Name</th>
<th style="{th_style}">Price</th>
<th style="{th_style}">Conf</th>
<th style="{th_style}">Date</th>
<th style="{th_style}">Status</th>
<th style="{th_style}">Note</th>
<th style="{th_style}">Business</th>
<th style="{th_style}">Rec</th>
</tr>
</thead>
<tbody>
{body}
</tbody>
</table>"""


def _render_upcoming_row(row: UpcomingIpoRow) -> str:
    return f"""<tr>
<td style="padding:8px;border-bottom:1px solid #eee;">{row.ticker or "—"}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_render_upcoming_name(row.name, row.edgar_confirmed)}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_format_currency(row.indicative_price)}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{row.price_confidence or "—"}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{row.expected_date or "—"}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{row.date_status or "—"}</td>
<td style="padding:8px;border-bottom:1px solid #eee;font-size:11px;">{row.date_note or "—"}</td>
<td style="padding:8px;border-bottom:1px solid #eee;max-width:150px;font-size:11px;">{row.business_summary or "—"}</td>
<td style="padding:8px;border-bottom:1px solid #eee;">{_format_recommendation(row.recommendation)}</td>
</tr>"""


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2%}"


def _format_pct_colored(value: float | None) -> str:
    """Format percentage with color (green for positive, red for negative)."""
    if value is None:
        return "—"
    pct_str = f"{value:.2%}"
    if value > 0:
        return f'<span style="color:#0a7c42;">{pct_str}</span>'
    elif value < 0:
        return f'<span style="color:#b91c1c;">{pct_str}</span>'
    return pct_str


def _format_currency(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def extract_recommendation(summary: str) -> str:
    """Extract recommendation from summary text. Returns — if no price/can't recommend."""
    if not summary:
        return "—"

    # Check if this is a "cannot recommend" case (no price).
    if "cannot recommend" in summary.lower() or "can't recommend" in summary.lower():
        return "—"

    # Prefer structured patterns first (Decision: / Recommendation: / Action:).
    structured_patterns = [
        r"(?:\*\*)?Decision(?:\*\*)?[:\s—–-]+\s*(?:\*\*)?(STRONG BUY|BUY|PASS)(?:\*\*)?",
        r"(?:\*\*)?Recommendation(?:\*\*)?[:\s—–-]+\s*(?:\*\*)?(STRONG BUY|BUY|PASS)(?:\*\*)?",
        r"(?:\*\*)?Action(?:\*\*)?[:\s—–-]+\s*(?:\*\*)?(STRONG BUY|BUY|PASS)(?:\*\*)?",
    ]
    for pattern in structured_patterns:
        match = re.search(pattern, summary, re.IGNORECASE)
        if match:
            return match.group(1).upper()

    # Fallback: match standalone STRONG BUY / BUY / PASS — order matters so
    # "STRONG BUY" is checked before "BUY" to avoid partial matches.
    for term in ("STRONG BUY", "PASS", "BUY"):
        # Word-boundary match; require surrounding non-alpha to avoid matching
        # inside words like "BUYBACK".
        pattern = rf"(?<![A-Z]){re.escape(term)}(?![A-Z])"
        if re.search(pattern, summary, re.IGNORECASE):
            return term
    return "—"


def _render_recent_name(name: str, source_quality: str) -> str:
    if source_quality == "single-source":
        return f"{name}*"
    return name


def _render_upcoming_name(name: str, edgar_confirmed: bool) -> str:
    if not edgar_confirmed:
        return f"{name}*"
    return name


def _group_charts(charts: list[ChartAsset]) -> dict[str, list[ChartAsset]]:
    grouped: dict[str, list[ChartAsset]] = {}
    for chart in charts:
        grouped.setdefault(chart.symbol, []).append(chart)
    return grouped


def _render_charts(charts: list[ChartAsset]) -> str:
    """Render charts using table layout for email client compatibility (Outlook, Gmail, etc.)."""
    if not charts:
        return ""
    # Limit to 2 charts max and use table layout
    charts = charts[:2]
    if len(charts) == 1:
        chart = charts[0]
        return f"""<table class="chart-table" cellpadding="0" cellspacing="0" border="0">
<tr>
<td class="chart-cell" style="width:50%;padding:4px;text-align:center;vertical-align:top;">
<img src="cid:{chart.content_id}" alt="{chart.symbol} {chart.window_label}" style="max-width:100%;height:auto;border:1px solid #e0e0e0;border-radius:4px;" />
<div class="chart-label" style="font-size:11px;color:#666;margin-top:4px;">{chart.window_label}</div>
</td>
</tr>
</table>"""
    # Two charts side by side
    c1, c2 = charts[0], charts[1]
    return f"""<table class="chart-table" cellpadding="0" cellspacing="0" border="0" style="width:100%;margin-bottom:12px;">
<tr>
<td class="chart-cell" style="width:50%;padding:4px;text-align:center;vertical-align:top;">
<img src="cid:{c1.content_id}" alt="{c1.symbol} {c1.window_label}" style="max-width:100%;height:auto;border:1px solid #e0e0e0;border-radius:4px;" />
<div class="chart-label" style="font-size:11px;color:#666;margin-top:4px;">{c1.window_label}</div>
</td>
<td class="chart-cell" style="width:50%;padding:4px;text-align:center;vertical-align:top;">
<img src="cid:{c2.content_id}" alt="{c2.symbol} {c2.window_label}" style="max-width:100%;height:auto;border:1px solid #e0e0e0;border-radius:4px;" />
<div class="chart-label" style="font-size:11px;color:#666;margin-top:4px;">{c2.window_label}</div>
</td>
</tr>
</table>"""


def _render_single_source_footnote(show: bool) -> str:
    if not show:
        return ""
    return '<div class="footnote" style="font-size:11px;color:#666;margin-bottom:12px;font-style:italic;">* Single-source entry — data may be less reliable</div>'


def _render_edgar_footnote(show: bool) -> str:
    if not show:
        return ""
    return '<div class="footnote" style="font-size:11px;color:#666;margin-bottom:12px;font-style:italic;">* No confirmation on EDGAR</div>'


def _format_recommendation(rec: str) -> str:
    """Format recommendation with color styling for email."""
    rec_upper = rec.upper().strip()
    if rec_upper == "STRONG BUY":
        return f'<span style="color:#0a7c42;font-weight:600;">{rec}</span>'
    elif rec_upper == "BUY":
        return f'<span style="color:#1a73e8;font-weight:600;">{rec}</span>'
    elif rec_upper == "PASS":
        return f'<span style="color:#b91c1c;font-weight:600;">{rec}</span>'
    return rec
