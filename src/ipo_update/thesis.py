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


def _strip_json_block(text: str) -> str:
    """Remove the first top-level JSON object (the targets block) from the text.

    Returns the remaining prose so we don't accidentally truncate text that
    appears *after* the JSON block (the old code simply did ``text[:json_start]``).
    """
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_string = False
    escape_next = False
    end = None
    for idx in range(start, len(text)):
        ch = text[idx]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end is None:
        return text[:start].strip()
    # Remove the JSON block and stitch surrounding text together.
    return (text[:start].rstrip() + "\n\n" + text[end:].lstrip()).strip()


def _load_template(template_path: Path) -> str:
    if not template_path.exists():
        raise FileNotFoundError(f"Research template not found: {template_path}")
    return template_path.read_text(encoding="utf-8").strip()


def build_rich_identifier(
    ticker: str | None,
    name: str | None,
    business_summary: str | None = None,
) -> str:
    """Build a clear, disambiguating identifier for LLM prompts.
    
    Examples:
    - With ticker: "MDLN (Medline Inc.)"
    - No ticker, has summary: "Space Exploration Technologies Corp. — aerospace manufacturer"
    - No ticker, no summary: "OpenAI, Inc. — [use web search to identify]"
    """
    if ticker and name:
        return f"{ticker} ({name})"
    if ticker:
        return ticker
    if name and business_summary:
        # Truncate long summaries
        summary = business_summary[:100].strip()
        if len(business_summary) > 100:
            summary = summary.rsplit(" ", 1)[0] + "..."
        return f"{name} — {summary}"
    if name:
        return f"{name} — [use web search to identify this company]"
    return "Unknown Company"


def _build_baseline_prompt(
    identifier: str,
    template: str,
    ticker: str | None = None,
    name: str | None = None,
    business_summary: str | None = None,
) -> str:
    # Build a rich identifier for the prompt
    rich_id = build_rich_identifier(ticker, name, business_summary)
    base_prompt = template.replace("<Insert Identifier>", rich_id)
    # Fallback for old template format
    base_prompt = base_prompt.replace("<Insert Ticker>", rich_id)
    
    # Add context about what we know
    context_lines = []
    if ticker:
        context_lines.append(f"- Ticker: {ticker}")
    if name:
        context_lines.append(f"- Company Name: {name}")
    if business_summary:
        context_lines.append(f"- Business: {business_summary}")
    
    context_block = ""
    if context_lines:
        context_block = "\n\nKNOWN INFORMATION:\n" + "\n".join(context_lines)
    
    enhanced_prompt = f"""{base_prompt}{context_block}

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
        perf_info += f"- Since IPO: {perf_since_ipo:.2%}\n"
    if return_1w is not None or return_1m is not None:
        parts = []
        if return_1w is not None:
            parts.append(f"{return_1w:.2%} (1W)")
        if return_1m is not None:
            parts.append(f"{return_1m:.2%} (1M)")
        perf_info += f"- Recent: {', '.join(parts)}\n"
    news_text = "\n".join(
        f"- {item.get('title')} ({item.get('source')})" for item in news_items[:3]
    )
    return f"""Write a CONCISE writeup for recent IPO: {identifier}.

DATA:
- IPO Date: {ipo_date or "unknown"}
{price_info}{perf_info}{targets_info}
{news_text}

BASELINE (for reference only—do NOT repeat):
{baseline[:1500]}{"..." if len(baseline) > 1500 else ""}

OUTPUT FORMAT (follow this EXACTLY):
- **What they do**: 1-2 sentences on business model.
- **Post-IPO**: 2 sentences on price action since listing.
- **Targets**: Base $X / Bull $Y / Bear $Z — one line each with brief rationale.
- **5x potential**: One sentence—realistic or not.
- **Decision**: STRONG BUY / BUY / PASS — state entry price if buying.

EXAMPLE OUTPUT (use this style):
**What they do**: MDLN distributes medical supplies to hospitals/clinics with sticky relationships and recurring demand.

**Post-IPO**: Up 52% since listing; strong momentum (+9% 1W) suggests buy-side confidence in the cash-flow story.

**Targets**:
- Base $58: Steady deleveraging + margin stability.
- Bull $110: Above-trend growth + premium multiple.
- Bear $28: Margin compression or execution slip.

**5x potential**: Not realistic in 1-2 years without exceptional discontinuity.

**Decision**: BUY — accumulate on pullbacks toward $40-42.

RULES:
- Do NOT say "Executive Summary" anywhere.
- Do NOT use numbered sections (1), 2), 3)) — use bullets.
- Do NOT use markdown headers (###).
- Bold only key terms (ticker, prices, recommendation), not entire sentences.
- Use markdown links [text](url) for citations.
- Keep it under 250 words total."""


def _build_upcoming_summary_prompt(
    identifier: str,
    baseline: str,
    expected_date: str | None,
    indicative_price: float | None,
    price_confidence: str | None,
) -> str:
    has_price = indicative_price is not None
    price_line = f"Indicative price: ${indicative_price:.2f} ({price_confidence} confidence)" if has_price else "Price: TBD (not yet disclosed)"
    
    decision_instruction = """- **Decision**: STRONG BUY / BUY / PASS — with participation price range.""" if has_price else """- **Decision**: Cannot recommend without price. State what valuation metrics to watch (e.g., "participate only if priced below X times revenue")."""
    
    return f"""Write a CONCISE preview for upcoming IPO: {identifier}.

DATA:
- Expected date: {expected_date or "TBD"}
- {price_line}

BASELINE (for reference only—do NOT repeat):
{baseline[:1500]}{"..." if len(baseline) > 1500 else ""}

OUTPUT FORMAT (follow this EXACTLY):
- **What they do**: 1-2 sentences on business model.
- **Bull/Bear**: Key upside case vs key risk (2-3 sentences total).
- **Targets**: Base $X / Bull $Y / Bear $Z — or valuation framework if price unknown.
- **5x potential**: One sentence.
{decision_instruction}

EXAMPLE OUTPUT (use this style for upcoming IPO WITH price):
**What they do**: RIKU operates Japanese restaurants in the US with a scalable franchise model.

**Bull/Bear**: Bull case is proven unit economics + expansion runway; bear case is restaurant execution risk and macro sensitivity.

**Targets**:
- Base $8: Steady store growth + margin stability.
- Bull $18: Faster expansion + premium brand multiple.
- Bear $3: Growth stalls or same-store declines.

**5x potential**: Possible if expansion exceeds expectations, but not base case.

**Decision**: BUY — participate at IPO price ($5) with small sizing; add on execution proof.

EXAMPLE OUTPUT (use this style for upcoming IPO WITHOUT price):
**What they do**: STUB operates a secondary ticketing marketplace monetizing via fees.

**Bull/Bear**: Bull case is durable marketplace liquidity + operating leverage; bear case is fee regulation and event-cycle volatility.

**Targets**: Use EV/Revenue framework—participate only if priced below 3-4x forward revenue.

**5x potential**: Possible but requires multi-year category leadership proof.

**Decision**: Cannot recommend without price. Participate only if IPO implies reasonable EV/Revenue vs marketplace comps.

RULES:
- Do NOT say "Executive Summary" anywhere.
- Do NOT use numbered sections — use bullets.
- Do NOT use markdown headers (###).
- Bold only key terms, not entire sentences.
- Use markdown links [text](url) for citations.
- Keep it under 200 words total."""


def _markdown_to_html(text: str) -> str:
    """Convert markdown-formatted text to HTML for email display.
    
    Handles:
    - **bold** -> <strong>
    - *italic* -> <em>
    - [text](url) -> <a href="url">text</a>
    - Paragraphs separated by blank lines
    - #, ##, ###, #### headers
    - Bullet lists
    """
    if not text:
        return text
    
    # Convert markdown links [text](url) to HTML <a> tags
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href="\2" style="color:#0066cc;text-decoration:none;">\1</a>',
        text
    )
    
    # Convert **bold** to <strong>
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    
    # Convert *italic* to <em> (but not if part of bold)
    text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", text)
    
    # Remove any bare URLs that appear after citations
    text = re.sub(r'\s*\(\s*https?://[^\s)]+\s*\)', '', text)
    
    # Split into paragraphs
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        if "\n" in text:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        else:
            paragraphs = [text.strip()]
    
    html_paragraphs = []
    for para in paragraphs:
        # Skip paragraphs that are just bare URLs
        if re.match(r'^https?://\S+$', para.strip()):
            continue

        # Process line by line — detect bullets & headers BEFORE joining.
        lines = para.split("\n")
        bullet_items: list[str] = []
        non_bullet_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            # Markdown header → bold inline
            if stripped.startswith("#### "):
                stripped = f"<strong>{stripped[5:].strip()}</strong>"
            elif stripped.startswith("### "):
                stripped = f"<strong>{stripped[4:].strip()}</strong>"
            elif stripped.startswith("## "):
                stripped = f"<strong>{stripped[3:].strip()}</strong>"
            elif stripped.startswith("# "):
                stripped = f"<strong>{stripped[2:].strip()}</strong>"

            # Bullet point?
            if stripped.startswith("- "):
                bullet_items.append(stripped[2:].strip())
            elif stripped.startswith("• "):
                bullet_items.append(stripped[2:].strip())
            else:
                # If we were accumulating bullets and hit a non-bullet line,
                # flush them first.
                if bullet_items:
                    li = "".join(f'<li style="margin:4px 0;">{b}</li>' for b in bullet_items)
                    html_paragraphs.append(f'<ul style="margin:8px 0;padding-left:20px;">{li}</ul>')
                    bullet_items = []
                non_bullet_lines.append(stripped)

        # Flush remaining bullets
        if bullet_items:
            li = "".join(f'<li style="margin:4px 0;">{b}</li>' for b in bullet_items)
            html_paragraphs.append(f'<ul style="margin:8px 0;padding-left:20px;">{li}</ul>')

        # Flush remaining non-bullet text
        if non_bullet_lines:
            joined = " ".join(non_bullet_lines)
            html_paragraphs.append(f'<p style="margin:0 0 12px 0;">{joined}</p>')

    return "\n".join(html_paragraphs) if html_paragraphs else f"<p>{text}</p>"


def generate_baseline(
    identifier: str,
    client,
    model: str,
    template_path: Path,
    thesis_dir: Path,
    ticker: str | None = None,
    name: str | None = None,
    business_summary: str | None = None,
) -> tuple[str | None, Targets | None]:
    logger = get_logger(__name__)
    rich_id = build_rich_identifier(ticker, name, business_summary)
    logger.info(f"Generating baseline thesis for {rich_id}")
    template = _load_template(template_path)
    prompt = _build_baseline_prompt(
        identifier=identifier,
        template=template,
        ticker=ticker,
        name=name,
        business_summary=business_summary,
    )
    response = call_responses_with_web_search(client, model, prompt)
    targets = parse_targets_from_response(response.text)
    thesis_text = _strip_json_block(response.text) if targets else response.text
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
