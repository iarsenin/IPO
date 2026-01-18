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
    sources: list[dict]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ticker": self.ticker,
            "ipo_date": self.ipo_date.isoformat() if self.ipo_date else None,
            "ipo_price": self.ipo_price,
            "exchange": self.exchange,
            "sources": self.sources,
        }


@dataclass(frozen=True)
class UpcomingIpo:
    name: str
    ticker: str | None
    expected_date: str | None
    date_status: str | None
    indicative_price: float | None
    price_confidence: str | None
    business_summary: str | None
    sources: list[dict]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ticker": self.ticker,
            "expected_date": self.expected_date,
            "date_status": self.date_status,
            "indicative_price": self.indicative_price,
            "price_confidence": self.price_confidence,
            "business_summary": self.business_summary,
            "sources": self.sources,
        }


def _normalize_ticker(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().upper()
    return text or None


def _normalize_price(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("$", "").strip())
    except ValueError:
        return None


def _parse_recent_items(items: list[dict]) -> list[RecentIpo]:
    parsed: list[RecentIpo] = []
    for item in items:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        ipo_date = parse_date(item.get("ipo_date"))
        parsed.append(
            RecentIpo(
                name=name,
                ticker=_normalize_ticker(item.get("ticker")),
                ipo_date=ipo_date,
                ipo_price=_normalize_price(item.get("ipo_price")),
                exchange=str(item.get("exchange", "")).strip() or None,
                sources=list(item.get("sources", []) or []),
            )
        )
    return parsed


def _parse_upcoming_items(items: list[dict]) -> list[UpcomingIpo]:
    parsed: list[UpcomingIpo] = []
    for item in items:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        parsed.append(
            UpcomingIpo(
                name=name,
                ticker=_normalize_ticker(item.get("ticker")),
                expected_date=str(item.get("expected_date", "")).strip() or None,
                date_status=str(item.get("date_status", "")).strip() or None,
                indicative_price=_normalize_price(item.get("indicative_price")),
                price_confidence=str(item.get("price_confidence", "")).strip() or None,
                business_summary=str(item.get("business_summary", "")).strip() or None,
                sources=list(item.get("sources", []) or []),
            )
        )
    return parsed


def fetch_recent_ipos(client, model: str, window_days: int) -> list[RecentIpo]:
    logger = get_logger(__name__)
    cutoff = date.today() - timedelta(days=window_days)
    logger.info(f"Fetching recent IPOs (last {window_days} days, cutoff {cutoff.isoformat()})")
    prompt = f"""You are finding RECENTLY PRICED IPOs. Use web search to find IPOs that priced in the last {window_days} days.

IMPORTANT:
- The list must include only IPOs priced on or after {cutoff.isoformat()}.
- Give sources but do NOT limit yourself to any set of sources. You can search anywhere.
- Suggested sources (not a limit): Nasdaq IPO calendar, NYSE IPO listings, Renaissance Capital, SEC filings, major financial media.

Return JSON ONLY (no markdown) with this structure:
[
  {{
    "ticker": "TICKER",
    "name": "Company Name",
    "ipo_date": "YYYY-MM-DD",
    "ipo_price": 12.0,
    "exchange": "NYSE/Nasdaq/Other",
    "sources": [
      {{"title": "...", "url": "...", "date": "YYYY-MM-DD"}}
    ]
  }}
]

Notes:
- If ticker is unknown, set it to null.
- If IPO price is unclear, set it to null and still include the company.
- Keep sources minimal but credible.
"""
    response = call_responses_with_web_search(client, model, prompt)
    data = extract_json_block(response.text)
    if not isinstance(data, list):
        logger.warning("Recent IPO list parsing failed; expected JSON array")
        return []
    ipos = _parse_recent_items(data)
    logger.info(f"Parsed {len(ipos)} recent IPOs from model response")
    return ipos


def fetch_upcoming_ipos(client, model: str, window_days: int) -> list[UpcomingIpo]:
    logger = get_logger(__name__)
    logger.info(f"Fetching upcoming IPOs (next {window_days} days)")
    prompt = f"""You are finding UPCOMING IPOs expected in the next {window_days} days.

IMPORTANT:
- Provide upcoming IPO candidates with best available dates and prices.
- Use web search and do NOT limit sources to any set. Suggested sources only: Nasdaq/NYSE IPO calendars, Renaissance Capital, SEC filings, company IR pages, major financial media.
- If ticker isn't available, use company name and provide a clear, disambiguating description.

Return JSON ONLY (no markdown) with this structure:
[
  {{
    "ticker": "TICKER or null",
    "name": "Company Name",
    "expected_date": "YYYY-MM-DD or 'Q1 2026' if not exact",
    "date_status": "set/expected/rumored",
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
