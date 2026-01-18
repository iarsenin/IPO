from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re

import requests

from .logger import get_logger
from .llm_utils import call_responses_with_web_search, extract_json_block


@dataclass(frozen=True)
class ThesisSummary:
    identifier: str
    summary: str
    updated: bool


@dataclass
class Targets:
    base_target: float
    bull_target: float
    bear_target: float
    target_rationale: dict[str, str]
    target_changes: dict[str, str] | None = None
    current_price: float | None = None
    entry_price: float | None = None
    target_reached: bool = False
    progress_to_base: float | None = None
    key_metrics: list[dict] | None = None
    watchlist: list[dict] | None = None
    investment_horizon: str | None = None
    risk_level: str | None = None
    last_updated: str | None = None
    updated_by: str | None = None


def load_baseline(thesis_dir: Path, identifier: str) -> str | None:
    path = thesis_dir / identifier / "baseline.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def save_baseline(thesis_dir: Path, identifier: str, content: str) -> Path:
    path = thesis_dir / identifier / "baseline.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def load_full_thesis(thesis_dir: Path, identifier: str) -> str | None:
    path = thesis_dir / identifier / "full_thesis.md"
    if not path.exists():
        return load_baseline(thesis_dir, identifier)
    return path.read_text(encoding="utf-8").strip()


def save_full_thesis(thesis_dir: Path, identifier: str, content: str) -> Path:
    path = thesis_dir / identifier / "full_thesis.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def load_update(thesis_dir: Path, identifier: str, date_str: str | None = None) -> str | None:
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    path = thesis_dir / identifier / f"update_{date_str}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip()


def save_update(thesis_dir: Path, identifier: str, content: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d")
    path = thesis_dir / identifier / f"update_{timestamp}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def load_targets(thesis_dir: Path, identifier: str) -> Targets | None:
    path = thesis_dir / identifier / "targets.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Targets(**data)
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger = get_logger(__name__)
        logger.error(f"Failed to load targets for {identifier}: {exc}")
        return None


def save_targets(thesis_dir: Path, identifier: str, targets: Targets) -> Path:
    path = thesis_dir / identifier / "targets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in targets.__dict__.items() if v is not None}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def parse_targets_from_response(response_text: str) -> Targets | None:
    logger = get_logger(__name__)
    data = extract_json_block(response_text)
    if not isinstance(data, dict):
        logger.warning("No JSON targets block found in response")
        return None
    try:
        base_target = float(data.get("base_target", 0))
        bull_target = float(data.get("bull_target", 0))
        bear_target = float(data.get("bear_target", 0))
        if base_target == 0 and bull_target == 0 and bear_target == 0:
            logger.warning("All targets are zero - invalid parsing")
            return None
        return Targets(
            base_target=base_target,
            bull_target=bull_target,
            bear_target=bear_target,
            target_rationale=data.get("target_rationale", {}),
            target_changes=data.get("target_changes"),
            key_metrics=data.get("key_metrics", []),
            watchlist=data.get("watchlist", []),
            investment_horizon=data.get("investment_horizon"),
            risk_level=data.get("risk_level"),
        )
    except (ValueError, TypeError, KeyError) as exc:
        logger.error(f"Failed to parse targets: {exc}")
        return None


def fetch_recent_news(symbol: str, api_key: str) -> list[dict]:
    logger = get_logger(__name__)
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": symbol,
        "apikey": api_key,
        "sort": "LATEST",
        "limit": 5,
    }
    try:
        logger.debug(f"AlphaVantage API call: fetching news for {symbol}")
        response = requests.get("https://www.alphavantage.co/query", params=params, timeout=30)
        logger.debug(f"AlphaVantage API response: news for {symbol} status={response.status_code}")
        response.raise_for_status()
        payload = response.json()
        feed = payload.get("feed", [])[:5]
        logger.info(f"AlphaVantage API success: {symbol} fetched {len(feed)} news items")
        return feed
    except requests.RequestException as exc:
        logger.error(f"AlphaVantage API request failed for news {symbol}: {type(exc).__name__} - {str(exc)[:100]}")
        raise


def _load_template(template_path: Path) -> str:
    if not template_path.exists():
        raise FileNotFoundError(f"Research template not found: {template_path}")
    return template_path.read_text(encoding="utf-8").strip()


def _build_baseline_prompt(identifier: str, template: str) -> str:
    base_prompt = template.replace("<Insert Ticker>", identifier)
    enhanced_prompt = f"""{base_prompt}

CRITICAL: You have access to web search. You MUST use it to find current, real information.
Suggested sources (not a limit): SEC filings, Nasdaq/NYSE IPO pages, Renaissance Capital, company IR pages,
Reuters/Bloomberg/FT/WSJ, industry research, and recent earnings/press releases.

Your job: create a deep, structured profile and assess whether this company has 5x upside potential.

After generating the thesis, YOU MUST determine price targets (base/bull/bear) and provide a JSON block:
{{
  "base_target": <price target>,
  "bull_target": <bull case target>,
  "bear_target": <bear case target>,
  "target_rationale": {{
    "base": "<reason>",
    "bull": "<reason>",
    "bear": "<reason>"
  }},
  "key_metrics": [{{"metric": "...", "current_value": "...", "target": "..."}}],
  "watchlist": [{{"event": "...", "expected_date": "...", "importance": "high/medium/low"}}],
  "investment_horizon": "<time horizon>",
  "risk_level": "<low/medium/high>"
}}

IMPORTANT: cite sources with URLs and publication dates when possible.
"""
    return enhanced_prompt


def _build_recent_summary_prompt(
    identifier: str,
    baseline: str,
    targets: Targets | None,
    ipo_date: str | None,
    ipo_price: float | None,
    current_price: float | None,
    perf_since_ipo: float | None,
    return_1w: float | None,
    return_1m: float | None,
    news_items: list[dict],
) -> str:
    targets_info = ""
    if targets:
        targets_info = (
            f"- Targets: Base ${targets.base_target:.2f}, Bull ${targets.bull_target:.2f}, "
            f"Bear ${targets.bear_target:.2f}\n"
        )
    price_info = ""
    if ipo_price is not None:
        price_info += f"- IPO Price: ${ipo_price:.2f}\n"
    if current_price is not None:
        price_info += f"- Current Price: ${current_price:.2f}\n"
    perf_info = ""
    if perf_since_ipo is not None:
        perf_info += f"- Performance since IPO: {perf_since_ipo:.2%}\n"
    if return_1w is not None or return_1m is not None:
        parts = []
        if return_1w is not None:
            parts.append(f"{return_1w:.2%} (1W)")
        if return_1m is not None:
            parts.append(f"{return_1m:.2%} (1M)")
        perf_info += f"- Recent performance: {', '.join(parts)}\n"
    news_text = "\n".join(
        f"- {item.get('title')} ({item.get('source')})" for item in news_items[:5]
    )
    return f"""Generate a several-paragraph executive summary for a RECENT IPO: {identifier}.

CONTEXT:
- IPO Date: {ipo_date or "unknown"}
{price_info}{perf_info}{targets_info}
- Deep Dive Profile:
{baseline}

- Recent News:
{news_text}

REQUIREMENTS:
1. Summarize company profile, business model, and IPO thesis (2-3 paragraphs).
2. Discuss post-IPO performance since pricing and the last month/week if available.
3. Provide price targets (base/bull/bear) and rationale in plain language.
4. Make a recommendation using EXACT format: "Decision: STRONG BUY/BUY/PASS".
5. Explicitly evaluate 5x upside potential; if realistic, explain why.
6. Mention an entry/participation price level if relevant.

Tone: professional, concise, actionable. Cite sources if you used web search."""


def _build_upcoming_summary_prompt(
    identifier: str,
    baseline: str,
    expected_date: str | None,
    indicative_price: float | None,
    price_confidence: str | None,
) -> str:
    price_line = f"Indicative price: ${indicative_price:.2f} (confidence: {price_confidence})" if indicative_price else "Indicative price: unknown"
    return f"""Generate a short executive summary for an UPCOMING IPO: {identifier}.

CONTEXT:
- Expected IPO date: {expected_date or "unknown"}
- {price_line}
- Deep Dive Profile:
{baseline}

REQUIREMENTS:
1. Provide a concise business description (few sentences).
2. State what makes this IPO interesting or risky.
3. Provide price targets (base/bull/bear) and rationale.
4. Recommend whether to participate using EXACT format: "Decision: STRONG BUY/BUY/PASS".
5. Explicitly evaluate 5x upside potential; if realistic, explain why.
6. Provide a participation price range if possible, and be clear on confidence.

Tone: professional, concise, actionable. Cite sources if you used web search."""


def _markdown_to_html(text: str) -> str:
    if not text:
        return text
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        if "\n" in text:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        else:
            paragraphs = [text.strip()]
    html_paragraphs = []
    for para in paragraphs:
        if para.startswith("# "):
            html_paragraphs.append(f"<h3>{para[2:].strip()}</h3>")
        elif para.startswith("## "):
            html_paragraphs.append(f"<h4>{para[3:].strip()}</h4>")
        else:
            html_paragraphs.append(f"<p>{para}</p>")
    return "\n".join(html_paragraphs) if html_paragraphs else f"<p>{text}</p>"


def generate_baseline(
    identifier: str,
    client,
    model: str,
    template_path: Path,
    thesis_dir: Path,
) -> tuple[str | None, Targets | None]:
    logger = get_logger(__name__)
    logger.info(f"Generating baseline thesis for {identifier}")
    template = _load_template(template_path)
    prompt = _build_baseline_prompt(identifier, template)
    response = call_responses_with_web_search(client, model, prompt)
    targets = parse_targets_from_response(response.text)
    thesis_text = response.text
    if targets:
        json_start = response.text.find("{")
        if json_start != -1:
            thesis_text = response.text[:json_start].strip()
    if not thesis_text:
        logger.error(f"Baseline thesis empty for {identifier}")
        return None, None
    save_baseline(thesis_dir, identifier, thesis_text)
    save_full_thesis(thesis_dir, identifier, thesis_text)
    if targets:
        targets.last_updated = datetime.now().strftime("%Y-%m-%d")
        targets.updated_by = "baseline_generation"
        save_targets(thesis_dir, identifier, targets)
    else:
        logger.warning(f"Targets missing for {identifier}; summary may be less actionable")
    logger.info(f"Baseline generated for {identifier}")
    return thesis_text, targets


def generate_recent_summary(
    identifier: str,
    baseline: str,
    targets: Targets | None,
    client,
    model: str,
    thesis_dir: Path,
    ipo_date: str | None,
    ipo_price: float | None,
    current_price: float | None,
    perf_since_ipo: float | None,
    return_1w: float | None,
    return_1m: float | None,
    news_items: list[dict],
) -> ThesisSummary:
    existing = load_update(thesis_dir, identifier)
    if existing:
        get_logger(__name__).info(f"Reusing cached summary for {identifier}")
        return ThesisSummary(identifier=identifier, summary=existing, updated=False)

    logger = get_logger(__name__)
    logger.info(f"Generating recent IPO summary for {identifier}")
    prompt = _build_recent_summary_prompt(
        identifier,
        baseline,
        targets,
        ipo_date,
        ipo_price,
        current_price,
        perf_since_ipo,
        return_1w,
        return_1m,
        news_items,
    )
    response = call_responses_with_web_search(client, model, prompt)
    summary = response.text.strip() or baseline
    save_update(thesis_dir, identifier, summary)
    logger.info(f"Recent IPO summary generated for {identifier} ({len(summary)} chars)")
    return ThesisSummary(identifier=identifier, summary=summary, updated=True)


def generate_upcoming_summary(
    identifier: str,
    baseline: str,
    targets: Targets | None,
    client,
    model: str,
    thesis_dir: Path,
    expected_date: str | None,
    indicative_price: float | None,
    price_confidence: str | None,
) -> ThesisSummary:
    existing = load_update(thesis_dir, identifier)
    if existing:
        get_logger(__name__).info(f"Reusing cached summary for {identifier}")
        return ThesisSummary(identifier=identifier, summary=existing, updated=False)

    logger = get_logger(__name__)
    logger.info(f"Generating upcoming IPO summary for {identifier}")
    prompt = _build_upcoming_summary_prompt(
        identifier,
        baseline,
        expected_date,
        indicative_price,
        price_confidence,
    )
    response = call_responses_with_web_search(client, model, prompt)
    summary = response.text.strip() or baseline
    save_update(thesis_dir, identifier, summary)
    logger.info(f"Upcoming IPO summary generated for {identifier} ({len(summary)} chars)")
    return ThesisSummary(identifier=identifier, summary=summary, updated=True)
