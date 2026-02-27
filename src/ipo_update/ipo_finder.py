from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .data_loader import parse_date
from .llm_utils import call_responses_with_web_search, extract_json_block
from .logger import get_logger


@dataclass(frozen=True)
class RecentIpo:
    name: str
    ticker: str | None
    ipo_date: date | None
    ipo_price: float | None
    exchange: str | None
    ipo_type: str | None
    date_kind: str | None
    date_confidence: str | None
    status: str | None
    date_note: str | None
    source_count: int
    source_quality: str
    sources: list[dict]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ticker": self.ticker,
            "ipo_date": self.ipo_date.isoformat() if self.ipo_date else None,
            "ipo_price": self.ipo_price,
            "exchange": self.exchange,
            "ipo_type": self.ipo_type,
            "date_kind": self.date_kind,
            "date_confidence": self.date_confidence,
            "status": self.status,
            "date_note": self.date_note,
            "source_count": self.source_count,
            "source_quality": self.source_quality,
            "sources": self.sources,
        }


@dataclass(frozen=True)
class UpcomingIpo:
    name: str
    ticker: str | None
    expected_date: str | None
    date_status: str | None
    date_confidence: str | None
    date_note: str | None
    stage: str | None  # pricing_announced, roadshow, effective, filed, rumored
    indicative_price: float | None
    price_confidence: str | None
    business_summary: str | None
    ipo_type: str | None
    edgar_confirmed: bool
    edgar_note: str | None
    source_count: int
    source_quality: str
    sources: list[dict]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ticker": self.ticker,
            "expected_date": self.expected_date,
            "date_status": self.date_status,
            "date_confidence": self.date_confidence,
            "date_note": self.date_note,
            "stage": self.stage,
            "indicative_price": self.indicative_price,
            "price_confidence": self.price_confidence,
            "business_summary": self.business_summary,
            "ipo_type": self.ipo_type,
            "edgar_confirmed": self.edgar_confirmed,
            "edgar_note": self.edgar_note,
            "source_count": self.source_count,
            "source_quality": self.source_quality,
            "sources": self.sources,
        }


def _normalize_ticker(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().upper()
    return text or None


def _sanitize_name(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    # Remove markdown link formatting [Name](url)
    if text.startswith("[") and "](" in text and text.endswith(")"):
        text = text.split("](", 1)[0].lstrip("[").strip()
    # Strip accidental URL fragments
    text = text.replace("http://", "").replace("https://", "")
    # Remove parenthetical ticker/exchange noise
    text = text.replace("()", "").strip()
    return text.strip(" -\t") or None


def _normalize_price(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("$", "").strip())
    except ValueError:
        return None


def _parse_recent_items(items: list[dict], cutoff: date) -> list[RecentIpo]:
    parsed: list[RecentIpo] = []
    seen_tickers: dict[str, int] = {}  # ticker -> index in parsed list
    for item in items:
        name = _sanitize_name(item.get("name"))
        if not name:
            continue
        ipo_date = parse_date(item.get("ipo_date"))
        if ipo_date and ipo_date < cutoff:
            continue
        ipo_type = str(item.get("type", "")).strip().lower()
        if ipo_type == "spac":
            continue
        sources = list(item.get("sources", []) or [])
        source_count = len(sources)
        source_quality = "single-source" if source_count <= 1 else "multi-source"
        ticker = _normalize_ticker(item.get("ticker"))
        
        ipo = RecentIpo(
            name=name,
            ticker=ticker,
            ipo_date=ipo_date,
            ipo_price=_normalize_price(item.get("ipo_price")),
            exchange=str(item.get("exchange", "")).strip() or None,
            ipo_type=ipo_type or None,
            date_kind=str(item.get("date_kind", "")).strip() or None,
            date_confidence=str(item.get("date_confidence", "")).strip() or None,
            status=str(item.get("status", "")).strip() or None,
            date_note=str(item.get("date_note", "")).strip() or None,
            source_count=source_count,
            source_quality=source_quality,
            sources=sources,
        )
        
        # De-duplicate by ticker: keep entry with more sources or higher confidence
        if ticker and ticker in seen_tickers:
            existing_idx = seen_tickers[ticker]
            existing = parsed[existing_idx]
            # Keep the one with more sources, or higher confidence if tied
            confidence_rank = {"high": 3, "medium": 2, "low": 1}
            existing_conf = confidence_rank.get(existing.date_confidence or "", 0)
            new_conf = confidence_rank.get(ipo.date_confidence or "", 0)
            if ipo.source_count > existing.source_count or (
                ipo.source_count == existing.source_count and new_conf > existing_conf
            ):
                parsed[existing_idx] = ipo
            continue  # Skip adding duplicate
        
        if ticker:
            seen_tickers[ticker] = len(parsed)
        parsed.append(ipo)
    return parsed


def _parse_upcoming_items(items: list[dict]) -> list[UpcomingIpo]:
    parsed: list[UpcomingIpo] = []
    seen_tickers: dict[str, int] = {}  # ticker -> index in parsed list
    seen_names: dict[str, int] = {}  # name (normalized) -> index for no-ticker entries
    for item in items:
        name = _sanitize_name(item.get("name"))
        if not name:
            continue
        ipo_type = str(item.get("type", "")).strip().lower()
        if ipo_type == "spac":
            continue
        expected_date = str(item.get("expected_date", "")).strip() or None
        date_note = str(item.get("date_note", "")).strip() or None
        parsed_date = parse_date(expected_date) if expected_date else None
        if parsed_date:
            # Weekend dates are suspicious for IPO pricing; keep but flag.
            if parsed_date.weekday() >= 5 and not date_note:
                date_note = "Weekend date; verify pricing or first trade date"
        if parsed_date and parsed_date < date.today():
            # An "upcoming" IPO with a past date has likely already priced — skip it.
            get_logger(__name__).debug(
                f"Dropping upcoming IPO '{name}' — date {parsed_date} is in the past"
            )
            continue
        sources = list(item.get("sources", []) or [])
        source_count = len(sources)
        source_quality = "single-source" if source_count <= 1 else "multi-source"
        edgar_confirmed = bool(item.get("edgar_confirmed", False))
        edgar_note = str(item.get("edgar_note", "")).strip() or None
        if not edgar_confirmed and not edgar_note:
            edgar_note = "No confirmation on EDGAR"
        stage = str(item.get("stage", "")).strip().lower() or None
        ticker = _normalize_ticker(item.get("ticker"))
        
        ipo = UpcomingIpo(
            name=name,
            ticker=ticker,
            expected_date=expected_date,
            date_status=str(item.get("date_status", "")).strip() or None,
            date_confidence=str(item.get("date_confidence", "")).strip() or None,
            date_note=date_note,
            stage=stage,
            indicative_price=_normalize_price(item.get("indicative_price")),
            price_confidence=str(item.get("price_confidence", "")).strip() or None,
            business_summary=str(item.get("business_summary", "")).strip() or None,
            ipo_type=ipo_type or None,
            edgar_confirmed=edgar_confirmed,
            edgar_note=edgar_note,
            source_count=source_count,
            source_quality=source_quality,
            sources=sources,
        )
        
        # De-duplicate by ticker (or name if no ticker)
        if ticker and ticker in seen_tickers:
            existing_idx = seen_tickers[ticker]
            existing = parsed[existing_idx]
            if ipo.source_count > existing.source_count or (
                ipo.edgar_confirmed and not existing.edgar_confirmed
            ):
                parsed[existing_idx] = ipo
            continue
        
        name_key = name.lower().strip()
        if not ticker and name_key in seen_names:
            existing_idx = seen_names[name_key]
            existing = parsed[existing_idx]
            if ipo.source_count > existing.source_count or (
                ipo.edgar_confirmed and not existing.edgar_confirmed
            ):
                parsed[existing_idx] = ipo
            continue
        
        if ticker:
            seen_tickers[ticker] = len(parsed)
        else:
            seen_names[name_key] = len(parsed)
        parsed.append(ipo)
    return parsed


def fetch_recent_ipos(client, model: str, window_days: int) -> list[RecentIpo]:
    logger = get_logger(__name__)
    cutoff = date.today() - timedelta(days=window_days)
    logger.info(f"Fetching recent IPOs (last {window_days} days, cutoff {cutoff.isoformat()})")
    today = date.today()
    prompt = f"""Today's date is {today.isoformat()}.

You are building a COMPREHENSIVE list of RECENTLY PRICED US IPOs. Find all IPOs that priced or began trading on or after {cutoff.isoformat()} and up to {today.isoformat()}.

CRITICAL INSTRUCTIONS:
- **Search EACH ranked source below and aggregate all unique IPOs.**
- A comprehensive US IPO list for {window_days} days typically has 15–40 entries. If your list is significantly shorter, search again.
- **Only include operating companies.** Exclude SPACs, blank-check companies, shell listings, and unit offerings.
- **Name field must be the clean legal company name only** (no hyperlinks, no extra descriptors).
- Only include companies where the date is verified by sources.
- You MAY include single-source entries, but flag them with date_confidence="low".
- After building the list, do a second completeness pass to ensure no IPOs in the window were missed.

SEARCH THESE SOURCES (ranked):
1) Renaissance Capital – IPO Calendar (renaissancecapital.com)
2) IPO Scoop – IPO Calendar + Recently Priced (iposcoop.com)
3) SEC EDGAR (S-1 / F-1 filings) – for verification
4) Nasdaq IPO listings (nasdaq.com)
5) NYSE IPO Center (nyse.com)
6) Yahoo Finance IPO Calendar
7) StockAnalysis IPO Calendar (stockanalysis.com)
8) MarketWatch IPO Calendar

EXAMPLE ENTRIES:
{{"ticker": "MDLN", "name": "Medline Inc.", "ipo_date": "2025-12-17", "ipo_price": 22.0, "exchange": "NYSE", "status": "priced", "date_confidence": "high", "sources": [...]}}
{{"ticker": "WLTH", "name": "Wealthfront Corp.", "ipo_date": "2025-12-12", "ipo_price": 18.5, "exchange": "Nasdaq", "status": "priced", "date_confidence": "high", "sources": [...]}}
{{"ticker": "BLLN", "name": "BillionToOne, Inc.", "ipo_date": "2025-11-06", "ipo_price": 15.0, "exchange": "Nasdaq", "status": "priced", "date_confidence": "medium", "sources": [...]}}

Return JSON ONLY (no markdown) with this structure:
[
  {{
    "ticker": "TICKER",
    "name": "Company Name",
    "ipo_date": "YYYY-MM-DD",
    "ipo_price": 12.0,
    "exchange": "NYSE/Nasdaq/Other",
    "type": "operating_company",
    "status": "priced/started-trading",
    "date_confidence": "high/medium/low",
    "sources": [
      {{"title": "...", "url": "...", "date": "YYYY-MM-DD"}}
    ]
  }}
]

Notes:
- If ticker is unknown, set it to null.
- If IPO price is unclear, set it to null and still include the company.
- If the date is not verified by sources, exclude the company.
- Keep sources minimal but credible.
"""
    response = call_responses_with_web_search(client, model, prompt)
    data = extract_json_block(response.text)
    if not isinstance(data, list):
        logger.warning("Recent IPO list parsing failed; expected JSON array")
        return []
    ipos = _parse_recent_items(data, cutoff)
    logger.info(f"Parsed {len(ipos)} recent IPOs from model response")
    return ipos


def fetch_upcoming_ipos(client, model: str, window_days: int) -> list[UpcomingIpo]:
    logger = get_logger(__name__)
    today = date.today()
    horizon = today + timedelta(days=window_days)
    logger.info(f"Fetching upcoming IPOs (next {window_days} days, horizon {horizon.isoformat()})")
    prompt = f"""Today's date is {today.isoformat()}.

You are building a COMPREHENSIVE list of the US IPO pipeline. Include ALL companies that:
1. Have announced pricing dates in the next {window_days} days
2. Have active S-1/F-1 filings and could price soon
3. Are in roadshow or have effective registration
4. Are filed and waiting to go effective
5. Are rumored to be going public soon

CRITICAL INSTRUCTIONS:
- **Search EACH ranked source below and aggregate all unique IPO candidates.**
- A comprehensive US IPO pipeline typically has 15–40 entries. If your list is significantly shorter, search again.
- One source is acceptable (this is a rumor mill), but still check EDGAR for confirmation.
- If ticker isn't available, use company name.
- **Name field must be the clean legal company name only** (no hyperlinks, no extra descriptors).
- Single-source entries are acceptable; mark date_confidence accordingly.
- Exclude SPACs, blank-check companies, and shell listings.
- Check EDGAR for a filing; set edgar_confirmed accordingly. If not confirmed, set edgar_note to "No confirmation on EDGAR".
- If no specific date is available, use "TBD" for expected_date and appropriate stage.

SEARCH THESE SOURCES (ranked):
1) Renaissance Capital – IPO Calendar + News (renaissancecapital.com)
2) IPO Scoop – IPO Calendar + IPOs Recently Filed (iposcoop.com)
3) SEC EDGAR (S-1 / F-1 filings) – for confirmation
4) Nasdaq / NYSE IPO pages
5) Yahoo Finance IPO Calendar
6) MarketWatch IPO Calendar

IPO STAGES to include:
- "pricing_announced" – Has a confirmed pricing date
- "roadshow" – Currently in investor roadshow
- "effective" – Registration effective, waiting to price
- "filed" – S-1/F-1 filed, not yet effective
- "rumored" – Reported as planning IPO but not yet filed

EXAMPLE ENTRIES:
{{"ticker": "RDDT", "name": "Reddit Inc.", "expected_date": "2026-01-22", "date_status": "set", "stage": "pricing_announced", "edgar_confirmed": true, ...}}
{{"ticker": null, "name": "Stripe Inc.", "expected_date": "Q1 2026", "date_status": "rumored", "stage": "rumored", "edgar_confirmed": false, "edgar_note": "No confirmation on EDGAR", ...}}
{{"ticker": "PANW", "name": "Example Corp", "expected_date": "TBD", "date_status": "filed", "stage": "filed", "edgar_confirmed": true, ...}}

Return JSON ONLY (no markdown) with this structure:
[
  {{
    "ticker": "TICKER or null",
    "name": "Company Name",
    "expected_date": "YYYY-MM-DD or 'Q1 2026' or 'TBD'",
    "date_status": "set/expected/rumored/filed",
    "date_confidence": "high/medium/low",
    "date_note": "short clarification if needed",
    "stage": "pricing_announced/roadshow/effective/filed/rumored",
    "type": "operating_company/other",
    "edgar_confirmed": true/false,
    "edgar_note": "No confirmation on EDGAR if not confirmed",
    "indicative_price": 18.0,
    "price_confidence": "high/medium/low",
    "business_summary": "Few words on what the company does",
    "sources": [
      {{"title": "...", "url": "...", "date": "YYYY-MM-DD"}}
    ]
  }}
]
"""
    response = call_responses_with_web_search(client, model, prompt)
    data = extract_json_block(response.text)
    if not isinstance(data, list):
        logger.warning("Upcoming IPO list parsing failed; expected JSON array")
        return []
    ipos = _parse_upcoming_items(data)
    logger.info(f"Parsed {len(ipos)} upcoming IPOs from model response")
    return ipos
