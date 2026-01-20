# IPO

Local, Mac-first system that generates a **weekly IPO email** with:
- Recent IPO performance (last N days, default 90)
- Deep-dive profiles and executive summaries (OpenAI with web search)
- Upcoming IPO pipeline with recommendations and targets
- Charts per ticker (1M and 6M vs QQQ)

## Quick start
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:
- `ALPHA_VANTAGE_KEY` (required)
- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (optional; defaults to `gpt-5.2`)
- Gmail credentials for email sending:
  - `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `EMAIL_TO`, `EMAIL_TO_TEST`, `EMAIL_FROM`
- Optional:
  - `RECENT_IPO_WINDOW_DAYS` (default 90)
  - `UPCOMING_IPO_WINDOW_DAYS` (default 90)
  - `TIMEZONE` (default `America/Los_Angeles`)

Run:
```bash
bash scripts/run_report.sh
```

Test mode (sends to `EMAIL_TO_TEST`):
```bash
bash scripts/run_report.sh --test-email
```

Local-only (no email):
```bash
bash scripts/run_report.sh --no-email
```

## Outputs
- `reports/ipo_update_YYYYMMDD.html`
- `charts/*.png`
- `thesis/<IDENTIFIER>/baseline.md` and `update_YYYYMMDD.md`
- `log/ipo_update_YYYYMMDD_HHMMSS.log`

## Notes
- IPO lists are fetched fresh on each run (snapshots saved to `data/` for debugging).
- Recommendations use `STRONG BUY / BUY / PASS` and explicitly consider 5x upside potential.
- Upcoming IPOs without a disclosed price show "—" for recommendation (cannot evaluate without price).
- Duplicate tickers are automatically de-duplicated (keeps entry with most sources/highest confidence).
- SPACs and blank-check companies are filtered out.

## Details
This project generates a **weekly IPO intelligence email** focused on two distinct pipelines:
1. **Recent IPOs (default last 3 months)**: identify newly priced IPOs, build a deep‑dive profile, analyze post‑IPO performance, and produce an executive summary with targets and a recommendation.
2. **Upcoming IPOs (default next 3 months)**: identify likely upcoming offerings, research each company, and deliver a concise pre‑IPO summary with indicative pricing and participation guidance.

### Design goals
- **Local-first**: simple to run on a Mac with minimal dependencies.
- **Deterministic core logic**: calculation, table rendering, and charting are explicit and repeatable.
- **LLM used for synthesis**: the model is only used to summarize and reason; it does not drive core calculations.
- **Fresh data**: IPO lists are fetched fresh each run to ensure accuracy (snapshots saved for debugging).
- **Audit-friendly**: prompts request citations and store research outputs on disk.
- **Email-friendly HTML**: table-based layouts (no flexbox) for compatibility with Outlook, Gmail, and Mac Mail.

### Core workflow
1. **Fetch IPO lists** using OpenAI with web search:
   - Recent IPOs: last `RECENT_IPO_WINDOW_DAYS` (excludes SPACs, de-duplicates by ticker)
   - Upcoming IPOs: next `UPCOMING_IPO_WINDOW_DAYS` (checks EDGAR confirmation, excludes SPACs)
   - Sources: Renaissance Capital, IPO Scoop, SEC EDGAR, Nasdaq/NYSE, Yahoo Finance, MarketWatch
2. **Price & news data** from Alpha Vantage for recent IPO tickers.
3. **Performance metrics**:
   - Since IPO date (or first available price if IPO price is missing)
   - 1W / 1M returns where data exists
4. **Deep-dive profiles** (baseline thesis) using `templates/research_request.md`.
5. **Concise summaries** (not repetitive "executive summaries"):
   - Recent IPOs: post-IPO performance + targets + recommendation
   - Upcoming IPOs: participation guidance + targets (recommendation only if price is known)
6. **Charts**: two per ticker (1M and 6M vs QQQ), with "since listing" label if shorter.
7. **Email assembly**: table-based HTML with colored performance indicators and inline charts.

### Recommendation framework
Recommendations are intentionally simple and aligned to a **5x potential** lens:
- `STRONG BUY`: credible 5x upside with supportive fundamentals and timing
- `BUY`: attractive upside but less certain or requires more validation
- `PASS`: risk/reward not compelling or evidence insufficient

### Repository layout
```
data/          # cached IPO lists (JSON)
charts/        # generated chart images (gitignored)
reports/       # generated HTML reports (gitignored)
thesis/        # baseline + update markdown (gitignored)
templates/     # deep research prompt template
src/           # application code
scripts/       # run helpers
```

### Key modules
- `src/ipo_update/runner.py`: orchestrates the full pipeline and email send
- `src/ipo_update/ipo_finder.py`: IPO discovery using OpenAI web search
- `src/ipo_update/performance.py`: IPO performance metrics
- `src/ipo_update/thesis.py`: deep-dive generation + summaries + targets
- `src/ipo_update/charts.py`: ticker vs QQQ charts
- `src/ipo_update/email_builder.py`: HTML email composition

This section is intended to capture the project’s purpose and rationale so any human or LLM can extend the code without needing external context.