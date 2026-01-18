from dataclasses import dataclass
from os import getenv


@dataclass(frozen=True)
class Config:
    alpha_vantage_key: str
    openai_api_key: str | None
    openai_model: str
    gmail_user: str | None
    gmail_app_password: str | None
    email_to: str | None
    email_to_test: str | None
    email_from: str | None
    timezone: str
    recent_window_days: int
    upcoming_window_days: int


def _get_int(name: str, default: int) -> int:
    raw = getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def load_config() -> Config:
    alpha_vantage_key = getenv("ALPHA_VANTAGE_KEY", "").strip()
    if not alpha_vantage_key:
        raise ValueError("ALPHA_VANTAGE_KEY is required in the environment.")

    openai_api_key = getenv("OPENAI_API_KEY", "").strip() or None
    openai_model = getenv("OPENAI_MODEL", "gpt-5.2").strip() or "gpt-5.2"
    gmail_user = getenv("GMAIL_USER", "").strip() or None
    gmail_app_password = getenv("GMAIL_APP_PASSWORD", "").strip() or None
    email_to = getenv("EMAIL_TO", "").strip() or None
    email_to_test = getenv("EMAIL_TO_TEST", "").strip() or None
    email_from = getenv("EMAIL_FROM", "").strip() or gmail_user
    timezone = getenv("TIMEZONE", "America/Los_Angeles").strip() or "America/Los_Angeles"
    recent_window_days = _get_int("RECENT_IPO_WINDOW_DAYS", 90)
    upcoming_window_days = _get_int("UPCOMING_IPO_WINDOW_DAYS", 90)

    return Config(
        alpha_vantage_key=alpha_vantage_key,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        gmail_user=gmail_user,
        gmail_app_password=gmail_app_password,
        email_to=email_to,
        email_to_test=email_to_test,
        email_from=email_from,
        timezone=timezone,
        recent_window_days=recent_window_days,
        upcoming_window_days=upcoming_window_days,
    )
