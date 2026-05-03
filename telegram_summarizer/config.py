from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class SettingsError(RuntimeError):
    """Raised when environment configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    group: str | int
    gemini_api_key: str
    bot_token: str
    chat_id: str
    session_name: str
    downloads_dir: Path
    database_path: Path
    model_name: str
    summary_time: str
    timezone_name: str
    max_input_chars: int
    tesseract_cmd: str | None
    worker_concurrency: int
    max_retries: int
    request_timeout_seconds: int
    max_file_mb: int
    max_pdf_pages: int
    log_level: str

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


def _require_env(name: str, env: dict[str, str]) -> str:
    value = env.get(name)
    if not value:
        raise SettingsError(f"Missing required environment variable: {name}")
    return value


def _parse_group(raw: str) -> str | int:
    value = raw.strip()
    if value.startswith("-") and value[1:].isdigit():
        return int(value)
    if value.isdigit():
        return int(value)
    return value


def _parse_int(
    env: dict[str, str], name: str, default: str | None = None, minimum: int = 1
) -> int:
    raw = env.get(name, default)
    if raw is None:
        raise SettingsError(f"Missing required environment variable: {name}")
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, got: {raw}") from exc
    if value < minimum:
        raise SettingsError(f"{name} must be >= {minimum}, got: {value}")
    return value


def load_settings(env: dict[str, str] | None = None) -> Settings:
    resolved_env = env or dict(os.environ)
    return Settings(
        telegram_api_id=_parse_int(resolved_env, "TELEGRAM_API_ID"),
        telegram_api_hash=_require_env("TELEGRAM_API_HASH", resolved_env),
        group=_parse_group(_require_env("GROUP_USERNAME", resolved_env)),
        gemini_api_key=_require_env("GEMINI_API_KEY", resolved_env),
        bot_token=_require_env("BOT_TOKEN", resolved_env),
        chat_id=_require_env("CHAT_ID", resolved_env),
        session_name=resolved_env.get("SESSION_NAME", "session"),
        downloads_dir=Path(resolved_env.get("DOWNLOADS_DIR", "downloads")),
        database_path=Path(resolved_env.get("DATABASE_PATH", "bot.db")),
        model_name=resolved_env.get("GEMINI_MODEL", "gemini-2.5-flash"),
        summary_time=resolved_env.get("DAILY_SUMMARY_TIME", "22:00"),
        timezone_name=resolved_env.get("TIMEZONE", "Asia/Kolkata"),
        max_input_chars=_parse_int(
            resolved_env, "MAX_MODEL_INPUT_CHARS", default="15000"
        ),
        tesseract_cmd=resolved_env.get("TESSERACT_CMD") or None,
        worker_concurrency=_parse_int(
            resolved_env, "WORKER_CONCURRENCY", default="1"
        ),
        max_retries=_parse_int(resolved_env, "MAX_RETRIES", default="3"),
        request_timeout_seconds=_parse_int(
            resolved_env, "REQUEST_TIMEOUT_SECONDS", default="30"
        ),
        max_file_mb=_parse_int(resolved_env, "MAX_FILE_MB", default="20"),
        max_pdf_pages=_parse_int(resolved_env, "MAX_PDF_PAGES", default="50"),
        log_level=resolved_env.get("LOG_LEVEL", "INFO").upper(),
    )


def resolve_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SettingsError(
            f"Timezone '{name}' was not found. Use an IANA timezone name such as "
            f"'Asia/Kolkata'."
        ) from exc


def current_time(zone: ZoneInfo) -> datetime:
    return datetime.now(zone)


def parse_daily_time(value: str) -> dt_time:
    try:
        hour_str, minute_str = value.split(":", 1)
        return dt_time(hour=int(hour_str), minute=int(minute_str))
    except Exception as exc:
        raise SettingsError(
            "DAILY_SUMMARY_TIME must be in HH:MM format, for example 22:00"
        ) from exc


def next_run_after(now: datetime, target: dt_time) -> datetime:
    candidate = now.replace(
        hour=target.hour,
        minute=target.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def active_bucket_date(now: datetime, target: dt_time) -> date:
    if now.time() >= target:
        return (now + timedelta(days=1)).date()
    return now.date()
