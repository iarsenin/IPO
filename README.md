# IPO

Local, Mac-first system that generates **two weekly IPO emails**:
- **Priced IPOs** — recent performance, deep-dive profiles, and recommendations (last N days, default 90)
- **Expected IPOs** — upcoming pipeline with pre-IPO summaries, targets, and participation guidance

Each email includes clickable tickers in the summary table that jump to the detailed analysis, with a "↑ Back to top" link on every card. Charts (1M and 6M vs QQQ) are included in the Priced IPOs email.

## Quick start
```bash
cp .env.example .env
```

Fill in `.env`:
- `ALPHA_VANTAGE_KEY` (required)
- `OPENAI_API_KEY` (required)
- OpenAI model controls:
  - `OPENAI_DISCOVERY_MODEL` (default `OPENAI_MODEL`, normally `gpt-5.2`; uses web search)
  - `OPENAI_DISCOVERY_FALLBACK_MODEL` (default `OPENAI_MODEL`; used if discovery returns no parseable rows)
  - `OPENAI_BASELINE_MODEL` (default `gpt-5.4-mini`; uses web search)
  - `OPENAI_SUMMARY_MODEL` (default `gpt-5.4-mini`; text-only, capped output)
  - `OPENAI_MODEL` (legacy/global compatibility value; task-specific variables above are preferred)
- Gmail credentials for email sending:
  - `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `EMAIL_TO`, `EMAIL_TO_TEST`, `EMAIL_FROM`
- Optional:
  - `RECENT_IPO_WINDOW_DAYS` (default 90)
  - `UPCOMING_IPO_WINDOW_DAYS` (default 90)
  - `TIMEZONE` (default `America/Los_Angeles`)
  - `IPO_LOG_DIR` (default `~/Library/Logs/IPO`)
  - `IPO_LOG_RETENTION_DAYS` (default 30; set to 0 to disable cleanup)

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

Low-cost smoke test:
```bash
bash scripts/run_report.sh --no-email --use-cached-ipo-lists --limit-recent 2 --limit-upcoming 2
```

## Cost Controls
The runner separates model usage by task so the expensive freshness work stays targeted:
- **Discovery**: `OPENAI_DISCOVERY_MODEL` uses web search to find current priced/upcoming IPOs. Keep this on the strongest reliable model because an empty or incomplete list affects the whole report.
- **Baseline research**: `OPENAI_BASELINE_MODEL` uses web search only when a company has no cached `baseline.md`. Before creating a new baseline, the runner checks for exact identifier matches, normalized company-name matches, and matching cached baseline content so ticker/name drift does not trigger unnecessary web-search calls.
- **Summary refreshes**: `OPENAI_SUMMARY_MODEL` does not use web search. It receives a cleaned cached baseline excerpt, parsed target rationale, key metrics/watchlist, current price/performance data, and Alpha Vantage news already provided in the prompt.
- **Duplicate protection**: recent/upcoming lists are de-duplicated by ticker and normalized company name, ignoring punctuation and common legal suffixes such as Inc, Corp, Ltd, PLC, and Holdings.

Each OpenAI call logs `task`, `model`, token counts, web-search call count, and estimated dollars in `~/Library/Logs/IPO/ipo_update_*.log`. At the end of each run, the log includes an aggregate cost summary by task/model.

Expected cost behavior:
- A fresh full run still spends on discovery and any newly discovered companies that need baseline web research.
- A cached report rebuild with `--use-cached-ipo-lists` should avoid discovery costs.
- A cached rebuild without forced summary refresh should log no metered model calls unless a required cached summary is missing.
- `--force-refresh-summaries` regenerates the text-only investment briefs while reusing cached discovery and baseline research.

Useful low-cost modes:
```bash
# Rebuild reports from cached discovery data; no IPO discovery search.
bash scripts/run_report.sh --no-email --use-cached-ipo-lists

# Re-test a small slice of the report.
bash scripts/run_report.sh --no-email --use-cached-ipo-lists --limit-recent 2 --limit-upcoming 2

# Force today's summaries to regenerate while keeping discovery cached.
bash scripts/run_report.sh --no-email --use-cached-ipo-lists --force-refresh-summaries
```

## Outputs
- `reports/ipo_update_priced_YYYYMMDD.html` — Priced IPOs email
- `reports/ipo_update_expected_YYYYMMDD.html` — Expected IPOs email
- `charts/*.png`
- `thesis/<IDENTIFIER>/baseline.md` and `update_YYYYMMDD.md`
- `~/Library/Logs/IPO/ipo_update_YYYYMMDD_HHMMSS.log`

## Local runtime
The code can live in Google Drive and sync across machines. The run helper keeps machine-specific runtime files local:
- Python dependencies are installed into `~/.venvs/ipo` by default and refreshed when `requirements.txt` changes.
- Set `IPO_VENV=/path/to/venv` in your shell to use a different local virtualenv.
- Logs default to `~/Library/Logs/IPO` and old `ipo_update_*.log` files are deleted after 30 days.

## Notes
- IPO lists are fetched fresh on each run (snapshots saved to `data/` for debugging).
- Recommendations use `STRONG BUY / BUY / PASS` and explicitly consider 5x upside potential.
- Upcoming IPOs without a disclosed price show "—" for recommendation (cannot evaluate without price).
- Duplicate tickers and normalized company names are automatically de-duplicated; SPACs and blank-check companies are filtered out.

## Details
This project generates **two weekly IPO intelligence emails**, one per pipeline:
1. **Priced IPOs (default last 3 months)**: identify newly priced IPOs, build a deep‑dive profile, analyze post‑IPO performance, and produce an executive summary with targets and a recommendation.
2. **Expected IPOs (default next 3 months)**: identify likely upcoming offerings, research each company, and deliver a substantive pre‑IPO investment brief with indicative pricing and participation guidance.

Both emails include in-page navigation: tickers in the summary table are hyperlinks that jump directly to that company's detailed analysis card, and each card has a "↑ Back to top" link.

### Design goals
- **Local-first**: simple to run on a Mac with minimal dependencies.
- **Deterministic core logic**: calculation, table rendering, and charting are explicit and repeatable.
- **LLM used for synthesis**: the model is only used to summarize and reason; it does not drive core calculations.
- **Fresh data**: IPO lists are fetched fresh each run to ensure accuracy (snapshots saved for debugging).
- **Fail-fast**: API key is validated on startup; auth/billing errors abort immediately instead of producing empty reports.
- **Resilient**: transient API errors (rate-limit, network, server 5xx) are retried up to 3 times with exponential back-off.
- **Quality-preserving cost control**: discovery and baseline research use web search only where freshness matters; summary refreshes are text-only but include enough cached baseline and target context to preserve value proposition, scenario arguments, risks, and decision quality.
- **Cheap reruns**: `--use-cached-ipo-lists` skips discovery and rebuilds reports from `data/*.json`; cached baselines and summaries are reused when possible.
- **Audit-friendly**: prompts request citations where source URLs are available and store research outputs on disk.
- **Email-friendly HTML**: table-based layouts (no flexbox) for compatibility with Outlook, Gmail, and Mac Mail.

### Core workflow
0. **Pre-flight check**: validate the OpenAI API key by making a minimal call to each configured task model. If the key is invalid, revoked, or the account has no credits, the run aborts immediately with a clear error message — preventing long runs that produce empty reports.
1. **Fetch IPO lists** using OpenAI with web search (`OPENAI_DISCOVERY_MODEL`; prompts include today's date explicitly so the model knows the exact window):
   - Recent IPOs: last `RECENT_IPO_WINDOW_DAYS` (excludes SPACs, de-duplicates by ticker/name)
   - Upcoming IPOs: next `UPCOMING_IPO_WINDOW_DAYS` (checks EDGAR confirmation, excludes SPACs)
   - Sources: Renaissance Capital, IPO Scoop, SEC EDGAR, Nasdaq/NYSE, Yahoo Finance, MarketWatch
2. **Price & news data** from Alpha Vantage for recent IPO tickers.
3. **Performance metrics**:
   - Since IPO date (or first available price if IPO price is missing)
   - 1W / 1M returns where data exists
4. **Deep-dive profiles** (baseline thesis) using `templates/research_request.md` and `OPENAI_BASELINE_MODEL` with web search. Existing baselines are reused by exact identifier, normalized company name, or cached content match before any new baseline call is made.
5. **Investment briefs** using `OPENAI_SUMMARY_MODEL` without web search:
   - Recent IPOs: business/value proposition, current setup, why it could work, what can go wrong, scenario targets, and 5x/decision framing.
   - Upcoming IPOs: business/value proposition, IPO setup, bull case, bear case, scenario targets or valuation framework, and participation guidance (recommendation only if price is known).
   - Scenario targets must include assumptions; the model is instructed not to list base/bull/bear numbers without argument.
6. **Charts**: two per ticker (1M and 6M vs QQQ), with "since listing" label if shorter.
7. **Email assembly**: two separate HTML emails — Priced IPOs (with inline charts) and Expected IPOs. Each has a summary table with clickable ticker anchors and per-company detail cards with "↑ Back to top" navigation.

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
- `src/ipo_update/runner.py`: orchestrates the full pipeline, duplicate filtering, cache reuse, and email send
- `src/ipo_update/llm_utils.py`: OpenAI client creation, API validation, retry logic, usage/cost logging, JSON extraction
- `src/ipo_update/ipo_finder.py`: IPO discovery using OpenAI web search
- `src/ipo_update/performance.py`: IPO performance metrics
- `src/ipo_update/thesis.py`: baseline cache matching, deep-dive generation, investment briefs, targets, and markdown-to-HTML conversion
- `src/ipo_update/charts.py`: ticker vs QQQ charts
- `src/ipo_update/email_builder.py`: HTML email composition + recommendation extraction
- `src/ipo_update/data_loader.py`: AlphaVantage API calls + JSON snapshot I/O
- `src/ipo_update/config.py`: environment config loading
- `src/ipo_update/logger.py`: logging setup

This section is intended to capture the project’s purpose and rationale so any human or LLM can extend the code without needing external context.
