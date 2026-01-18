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
- IPO lists are cached in `data/` to reduce repeated LLM calls; use `--refresh` to force a new fetch.
- Recommendations use `STRONG BUY / BUY / PASS` and explicitly consider 5x upside potential.

## Details
This project generates a **weekly IPO intelligence email** focused on two distinct pipelines:
1. **Recent IPOs (default last 3 months)**: identify newly priced IPOs, build a deep‑dive profile, analyze post‑IPO performance, and produce an executive summary with targets and a recommendation.
2. **Upcoming IPOs (default next 3 months)**: identify likely upcoming offerings, research each company, and deliver a concise pre‑IPO summary with indicative pricing and participation guidance.

### Design goals
- **Local-first**: simple to run on a Mac with minimal dependencies.
- **Deterministic core logic**: calculation, table rendering, and charting are explicit and repeatable.
- **LLM used for synthesis**: the model is only used to summarize and reason; it does not drive core calculations.
- **Cost-aware**: IPO lists and summaries are cached to reduce repeated LLM and API calls.
- **Audit-friendly**: prompts request citations and store research outputs on disk.

### Core workflow
1. **Fetch IPO lists** using OpenAI with web search:
   - Recent IPOs: last `RECENT_IPO_WINDOW_DAYS`
   - Upcoming IPOs: next `UPCOMING_IPO_WINDOW_DAYS`
   - Sources are suggested but *not limited* (Nasdaq/NYSE calendars, SEC, major media, IR pages, etc.)
2. **Cache IPO lists** in `data/` to avoid re-fetching unless `--refresh` is used.
3. **Price & news data** from Alpha Vantage for recent IPO tickers.
4. **Performance metrics**:
   - Since IPO date (or first available price if IPO price is missing)
   - 1W / 1M returns where data exists
5. **Deep-dive profiles** (baseline thesis) using `templates/research_request.md`.
6. **Executive summaries**:
   - Recent IPOs: post-IPO performance + targets + recommendation
   - Upcoming IPOs: participation guidance + targets + recommendation
7. **Charts**: two per ticker (1M and 6M vs QQQ), with “since listing” fallback.
8. **Email assembly** with two tables + per-company writeups and inline charts.

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