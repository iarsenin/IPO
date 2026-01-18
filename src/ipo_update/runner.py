from __future__ import annotations

import argparse
from datetime import datetime
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import smtplib
import time

from dotenv import load_dotenv

from .charts import ChartRequest, generate_comparison_chart
from .config import load_config
from .data_loader import read_json, write_json, fetch_daily_adjusted_batch
from .email_builder import (
    ChartAsset,
    RecentIpoRow,
    UpcomingIpoRow,
    build_email_html,
    extract_recommendation,
)
from .ipo_finder import fetch_recent_ipos, fetch_upcoming_ipos
from .llm_utils import build_openai_client
from .logger import get_logger, setup_logging
from .performance import compute_ipo_performance
from .thesis import (
    ThesisSummary,
    fetch_recent_news,
    generate_baseline,
    generate_recent_summary,
    generate_upcoming_summary,
    load_baseline,
    load_targets,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate weekly IPO update email.")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--charts-dir", default="charts")
    parser.add_argument("--thesis-dir", default="thesis")
    parser.add_argument("--template-path", default="templates/research_request.md")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--log-dir", default="log")
    parser.add_argument("--recent-window-days", type=int, default=None)
    parser.add_argument("--upcoming-window-days", type=int, default=None)
    parser.add_argument("--refresh", action="store_true", help="Refresh IPO lists even if cache exists")
    parser.add_argument("--no-email", action="store_true", help="Suppress email sending (generate local report only)")
    parser.add_argument("--test-email", action="store_true", help="Send report to EMAIL_TO_TEST instead of EMAIL_TO")
    args = parser.parse_args()

    setup_logging(log_dir=args.log_dir)
    logger = get_logger(__name__)
    logger.info("Starting IPO update report generation")

    load_dotenv()
    config = load_config()

    recent_window_days = args.recent_window_days or config.recent_window_days
    upcoming_window_days = args.upcoming_window_days or config.upcoming_window_days

    if not config.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required to fetch IPO lists and summaries.")
    client = build_openai_client(config.openai_api_key)
    if client is None:
        raise RuntimeError("OpenAI client is unavailable.")

    data_dir = Path(args.data_dir)
    recent_cache_path = data_dir / "recent_ipos.json"
    upcoming_cache_path = data_dir / "upcoming_ipos.json"

    recent_ipos = _load_or_fetch_recent(
        recent_cache_path,
        client,
        config.openai_model,
        recent_window_days,
        args.refresh,
    )
    upcoming_ipos = _load_or_fetch_upcoming(
        upcoming_cache_path,
        client,
        config.openai_model,
        upcoming_window_days,
        args.refresh,
    )
    logger.info(f"Recent IPOs loaded: {len(recent_ipos)}")
    logger.info(f"Upcoming IPOs loaded: {len(upcoming_ipos)}")

    tickers = sorted({ipo.ticker for ipo in recent_ipos if ipo.ticker})
    logger.info(f"Recent IPO tickers: {', '.join(tickers) if tickers else 'none'}")

    series_map = {}
    benchmark_symbol = "QQQ"
    if tickers:
        series_map = fetch_daily_adjusted_batch([*tickers, benchmark_symbol], config.alpha_vantage_key)
    else:
        series_map = fetch_daily_adjusted_batch([benchmark_symbol], config.alpha_vantage_key)

    qqq_series = series_map.get(benchmark_symbol)
    charts_dir = Path(args.charts_dir)
    chart_assets: list[ChartAsset] = []

    thesis_dir = Path(args.thesis_dir)
    template_path = Path(args.template_path)

    recent_rows: list[RecentIpoRow] = []
    recent_summaries: dict[str, ThesisSummary] = {}
    last_av_call = 0.0
    min_interval = 0.2

    for ipo in recent_ipos:
        logger.info(f"Processing recent IPO: {ipo.name} ({ipo.ticker or 'no ticker'})")
        series = series_map.get(ipo.ticker) if ipo.ticker else None
        perf = compute_ipo_performance(ipo, series)

        identifier = ipo.ticker or ipo.name
        baseline = load_baseline(thesis_dir, identifier)
        targets = load_targets(thesis_dir, identifier)
        if not baseline:
            try:
                baseline, targets = generate_baseline(
                    identifier=identifier,
                    client=client,
                    model=config.openai_model,
                    template_path=template_path,
                    thesis_dir=thesis_dir,
                )
            except Exception as exc:
                logger.error(f"Baseline generation failed for {identifier}: {exc}")
                baseline = None
        if not baseline:
            baseline = "Baseline thesis generation failed."

        news_items: list[dict] = []
        if ipo.ticker:
            try:
                elapsed = time.monotonic() - last_av_call
                if elapsed < min_interval:
                    time.sleep(min_interval - elapsed)
                news_items = fetch_recent_news(ipo.ticker, config.alpha_vantage_key)
                last_av_call = time.monotonic()
            except Exception as exc:
                logger.warning(f"News fetch failed for {ipo.ticker}: {exc}")
                news_items = []

        try:
            summary = generate_recent_summary(
                identifier=identifier,
                baseline=baseline,
                targets=targets,
                client=client,
                model=config.openai_model,
                thesis_dir=thesis_dir,
                ipo_date=ipo.ipo_date.isoformat() if ipo.ipo_date else None,
                ipo_price=perf.ipo_price,
                current_price=perf.current_price,
                perf_since_ipo=perf.perf_since_ipo,
                return_1w=perf.return_1w,
                return_1m=perf.return_1m,
                news_items=news_items,
            )
        except Exception as exc:
            logger.error(f"Summary generation failed for {identifier}: {exc}")
            summary = ThesisSummary(identifier=identifier, summary=baseline, updated=False)

        recent_summaries[identifier] = summary
        recommendation = extract_recommendation(summary.summary)

        recent_rows.append(
            RecentIpoRow(
                name=ipo.name,
                ticker=ipo.ticker,
                ipo_date=ipo.ipo_date,
                ipo_price=perf.ipo_price,
                perf_since_ipo=perf.perf_since_ipo,
                return_1w=perf.return_1w,
                return_1m=perf.return_1m,
                recommendation=recommendation,
            )
        )

        if ipo.ticker and series is not None and qqq_series is not None:
            for window_days, label in ((30, "1M"), (180, "6M")):
                path = charts_dir / f"{ipo.ticker}_{label}.png"
                try:
                    generate_comparison_chart(
                        series=series,
                        benchmark_series=qqq_series,
                        request=ChartRequest(
                            symbol=ipo.ticker,
                            benchmark=benchmark_symbol,
                            window_days=window_days,
                            output_path=path,
                            purchase_date=ipo.ipo_date,
                        ),
                    )
                    chart_assets.append(
                        ChartAsset(
                            symbol=ipo.ticker,
                            window_label=label,
                            file_path=path,
                            content_id=f"{ipo.ticker}-{label}",
                        )
                    )
                    logger.info(f"Chart generated for {ipo.ticker} {label}")
                except ValueError as exc:
                    logger.warning(f"Skipping chart for {ipo.ticker} {label}: {exc}")

    upcoming_rows: list[UpcomingIpoRow] = []
    upcoming_summaries: dict[str, ThesisSummary] = {}
    for upcoming in upcoming_ipos:
        logger.info(f"Processing upcoming IPO: {upcoming.name} ({upcoming.ticker or 'no ticker'})")
        identifier = upcoming.ticker or upcoming.name
        baseline = load_baseline(thesis_dir, identifier)
        targets = load_targets(thesis_dir, identifier)
        if not baseline:
            try:
                baseline, targets = generate_baseline(
                    identifier=identifier,
                    client=client,
                    model=config.openai_model,
                    template_path=template_path,
                    thesis_dir=thesis_dir,
                )
            except Exception as exc:
                logger.error(f"Baseline generation failed for {identifier}: {exc}")
                baseline = None
        if not baseline:
            baseline = "Baseline thesis generation failed."

        try:
            summary = generate_upcoming_summary(
                identifier=identifier,
                baseline=baseline,
                targets=targets,
                client=client,
                model=config.openai_model,
                thesis_dir=thesis_dir,
                expected_date=upcoming.expected_date,
                indicative_price=upcoming.indicative_price,
                price_confidence=upcoming.price_confidence,
            )
        except Exception as exc:
            logger.error(f"Summary generation failed for {identifier}: {exc}")
            summary = ThesisSummary(identifier=identifier, summary=baseline, updated=False)
        upcoming_summaries[identifier] = summary
        recommendation = extract_recommendation(summary.summary)
        upcoming_rows.append(
            UpcomingIpoRow(
                name=upcoming.name,
                ticker=upcoming.ticker,
                indicative_price=upcoming.indicative_price,
                price_confidence=upcoming.price_confidence,
                expected_date=upcoming.expected_date,
                date_status=upcoming.date_status,
                business_summary=upcoming.business_summary,
                recommendation=recommendation,
            )
        )

    html = build_email_html(
        recent_rows=recent_rows,
        upcoming_rows=upcoming_rows,
        recent_summaries=recent_summaries,
        upcoming_summaries=upcoming_summaries,
        charts=chart_assets,
    )
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"ipo_update_{datetime.now().strftime('%Y%m%d')}.html"
    report_path.write_text(html, encoding="utf-8")
    logger.info(f"Report saved to {report_path}")

    if not args.no_email:
        email_to = config.email_to_test if args.test_email else config.email_to
        if args.test_email and not email_to:
            raise ValueError("EMAIL_TO_TEST is required when --test-email is set.")
        recipients = _parse_recipients(email_to)
        if not recipients:
            raise ValueError("Missing EMAIL_TO recipients in environment.")
        logger.info(f"Sending email to {', '.join(recipients)}")
        send_email(
            subject="IPO Weekly Update",
            html=html,
            charts=chart_assets,
            gmail_user=config.gmail_user,
            gmail_app_password=config.gmail_app_password,
            email_to=recipients,
            email_from=config.email_from,
        )
        logger.info("Email sent successfully")
    else:
        logger.info("Email sending suppressed (--no-email flag used)")

    logger.info("IPO update report generation completed")


def _load_or_fetch_recent(path: Path, client, model: str, window_days: int, refresh: bool):
    if not refresh:
        cached = read_json(path)
        if cached and cached.get("window_days") == window_days:
            items = cached.get("items", [])
            if items:
                logger = get_logger(__name__)
                logger.info(f"Using cached recent IPO list from {path}")
                from .ipo_finder import RecentIpo
                return [
                    RecentIpo(
                        name=item.get("name", ""),
                        ticker=item.get("ticker"),
                        ipo_date=datetime.fromisoformat(item["ipo_date"]).date() if item.get("ipo_date") else None,
                        ipo_price=item.get("ipo_price"),
                        exchange=item.get("exchange"),
                        sources=item.get("sources", []),
                    )
                    for item in items
                ]

    # Refresh cache when window changes or --refresh is set.
    recent_ipos = fetch_recent_ipos(client, model, window_days)
    write_json(
        path,
        {
            "generated_at": datetime.now().isoformat(),
            "window_days": window_days,
            "items": [ipo.to_dict() for ipo in recent_ipos],
        },
    )
    return recent_ipos


def _load_or_fetch_upcoming(path: Path, client, model: str, window_days: int, refresh: bool):
    if not refresh:
        cached = read_json(path)
        if cached and cached.get("window_days") == window_days:
            items = cached.get("items", [])
            if items:
                logger = get_logger(__name__)
                logger.info(f"Using cached upcoming IPO list from {path}")
                from .ipo_finder import UpcomingIpo
                return [
                    UpcomingIpo(
                        name=item.get("name", ""),
                        ticker=item.get("ticker"),
                        expected_date=item.get("expected_date"),
                        date_status=item.get("date_status"),
                        indicative_price=item.get("indicative_price"),
                        price_confidence=item.get("price_confidence"),
                        business_summary=item.get("business_summary"),
                        sources=item.get("sources", []),
                    )
                    for item in items
                ]

    # Refresh cache when window changes or --refresh is set.
    upcoming_ipos = fetch_upcoming_ipos(client, model, window_days)
    write_json(
        path,
        {
            "generated_at": datetime.now().isoformat(),
            "window_days": window_days,
            "items": [ipo.to_dict() for ipo in upcoming_ipos],
        },
    )
    return upcoming_ipos


def send_email(
    subject: str,
    html: str,
    charts: list[ChartAsset],
    gmail_user: str | None,
    gmail_app_password: str | None,
    email_to: list[str] | None,
    email_from: str | None,
) -> None:
    if not gmail_user or not gmail_app_password or not email_to or not email_from:
        raise ValueError("Missing Gmail credentials or recipient in environment.")

    message = MIMEMultipart("related")
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = ", ".join(email_to)

    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(html, "html"))
    message.attach(alternative)

    for chart in charts:
        with chart.file_path.open("rb") as handle:
            img = MIMEImage(handle.read())
            img.add_header("Content-ID", f"<{chart.content_id}>")
            img.add_header("Content-Disposition", "inline", filename=chart.file_path.name)
            message.attach(img)

    logger = get_logger(__name__)
    logger.debug("Connecting to Gmail SMTP server")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        logger.debug("Authenticating with Gmail")
        smtp.login(gmail_user, gmail_app_password)
        logger.debug("Sending email message")
        smtp.sendmail(email_from, email_to, message.as_string())


def _parse_recipients(value: str | None) -> list[str]:
    if not value:
        return []
    return [email.strip() for email in value.split(",") if email.strip()]


if __name__ == "__main__":
    main()
