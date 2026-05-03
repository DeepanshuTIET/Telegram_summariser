import asyncio
import io
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import fitz
import pytesseract
import requests
from PIL import Image, ImageOps
from dotenv import load_dotenv
from google import genai
from telethon import TelegramClient, events

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("telegram-summary-bot")


@dataclass
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    group: str | int
    gemini_api_key: str
    bot_token: str
    chat_id: str
    session_name: str = "session"
    downloads_dir: Path = Path("downloads")
    state_file: Path = Path("state.json")
    model_name: str = "gemini-2.5-flash"
    summary_time: str = "22:00"
    timezone_name: str = "Asia/Kolkata"
    max_input_chars: int = 15000
    tesseract_cmd: str | None = None


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _parse_group(raw: str) -> str | int:
    value = raw.strip()
    if value.startswith("-") and value[1:].isdigit():
        return int(value)
    if value.isdigit():
        return int(value)
    return value


def load_settings() -> Settings:
    return Settings(
        telegram_api_id=int(_require_env("TELEGRAM_API_ID")),
        telegram_api_hash=_require_env("TELEGRAM_API_HASH"),
        group=_parse_group(_require_env("GROUP_USERNAME")),
        gemini_api_key=_require_env("GEMINI_API_KEY"),
        bot_token=_require_env("BOT_TOKEN"),
        chat_id=_require_env("CHAT_ID"),
        session_name=os.getenv("SESSION_NAME", "session"),
        downloads_dir=Path(os.getenv("DOWNLOADS_DIR", "downloads")),
        state_file=Path(os.getenv("STATE_FILE", "state.json")),
        model_name=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        summary_time=os.getenv("DAILY_SUMMARY_TIME", "22:00"),
        timezone_name=os.getenv("TIMEZONE", "Asia/Kolkata"),
        max_input_chars=int(os.getenv("MAX_MODEL_INPUT_CHARS", "15000")),
        tesseract_cmd=os.getenv("TESSERACT_CMD"),
    )


class DailyStore:
    def __init__(self, state_file: Path):
        self.state_file = state_file
        self.lock = asyncio.Lock()
        self.current_bucket_date = date.today().isoformat()
        self.last_summary_date: str | None = None
        self.summaries: list[str] = []
        self._load()

    def _load(self) -> None:
        if not self.state_file.exists():
            return

        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.current_bucket_date = data.get(
                "current_bucket_date",
                data.get("current_date", self.current_bucket_date),
            )
            self.last_summary_date = data.get("last_summary_date")
            self.summaries = data.get("summaries", [])
            logger.info("Loaded persisted state from %s", self.state_file)
        except Exception as exc:
            logger.warning("Could not read state file %s: %s", self.state_file, exc)

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "current_bucket_date": self.current_bucket_date,
            "last_summary_date": self.last_summary_date,
            "summaries": self.summaries,
        }
        self.state_file.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    async def add_summary(self, summary: str, bucket_date: date) -> None:
        async with self.lock:
            if self.current_bucket_date != bucket_date.isoformat():
                self.current_bucket_date = bucket_date.isoformat()
                self.summaries = []
            self.summaries.append(summary)
            self._save()

    async def mark_sent(self, sent_for: date) -> None:
        async with self.lock:
            self.last_summary_date = sent_for.isoformat()
            if self.current_bucket_date == sent_for.isoformat():
                self.summaries = []
            self._save()

    async def get_summaries(self, bucket_date: date) -> list[str]:
        async with self.lock:
            if self.current_bucket_date != bucket_date.isoformat():
                return []
            return list(self.summaries)

    async def summary_already_sent(self, today: date) -> bool:
        async with self.lock:
            return self.last_summary_date == today.isoformat()


class GeminiSummarizer:
    def __init__(self, api_key: str, model_name: str, max_input_chars: int):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.max_input_chars = max_input_chars

    def summarize_text(self, text: str, heading: str | None = None) -> str:
        cleaned = text.strip()
        if not cleaned:
            return ""

        trimmed = cleaned[: self.max_input_chars]
        prompt_parts = [
            "Summarize the content into 5 to 10 sharp bullet points.",
            "Focus on actionable or important information and ignore noise.",
            "If the content contains market or trading calls, keep those clearly labeled.",
        ]
        if heading:
            prompt_parts.append(f"Context: {heading}")
        prompt_parts.append("Content:")
        prompt_parts.append(trimmed)
        prompt = "\n\n".join(prompt_parts)

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
        )
        return (response.text or "").strip()

    def summarize_many(self, chunks: list[str], heading: str) -> str:
        joined = "\n\n".join(chunks)
        if len(joined) <= self.max_input_chars:
            return self.summarize_text(joined, heading=heading)

        condensed: list[str] = []
        current = []
        current_len = 0
        for chunk in chunks:
            block = chunk.strip()
            if not block:
                continue
            if current and current_len + len(block) > self.max_input_chars:
                condensed.append(
                    self.summarize_text("\n\n".join(current), heading=heading)
                )
                current = [block]
                current_len = len(block)
            else:
                current.append(block)
                current_len += len(block)

        if current:
            condensed.append(self.summarize_text("\n\n".join(current), heading=heading))

        return self.summarize_text("\n\n".join(condensed), heading=heading)


def send_to_telegram(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    for chunk in chunk_text(text, 3900):
        response = requests.post(
            url,
            data={"chat_id": chat_id, "text": chunk},
            timeout=30,
        )
        response.raise_for_status()


def chunk_text(text: str, limit: int) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    lines = stripped.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = line
            continue

        start = 0
        while start < len(line):
            chunks.append(line[start : start + limit])
            start += limit

    if current:
        chunks.append(current)

    return chunks


def extract_pdf_text(path: Path) -> str:
    text_chunks: list[str] = []
    with fitz.open(path) as document:
        for page in document:
            text = page.get_text("text").strip()
            if text:
                text_chunks.append(text)
                continue

            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            image_bytes = pix.tobytes("png")
            with Image.open(io.BytesIO(image_bytes)) as page_image:
                text_chunks.append(extract_image_text_from_pil(page_image))

    return "\n\n".join(filter(None, text_chunks)).strip()


def extract_image_text(path: Path) -> str:
    with Image.open(path) as image:
        return extract_image_text_from_pil(image)


def extract_image_text_from_pil(image: Image.Image) -> str:
    processed = ImageOps.grayscale(image)
    return pytesseract.image_to_string(processed).strip()


def resolve_zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Timezone '%s' was not found. Falling back to server local time.",
            name,
        )
        return None


def current_time(zone: ZoneInfo | None) -> datetime:
    if zone is None:
        return datetime.now().astimezone()
    return datetime.now(zone)


def parse_daily_time(value: str) -> dt_time:
    try:
        hour_str, minute_str = value.split(":", 1)
        return dt_time(hour=int(hour_str), minute=int(minute_str))
    except Exception as exc:
        raise RuntimeError(
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


def describe_message(event: events.NewMessage.Event) -> str:
    sender_id = getattr(event.message, "sender_id", "unknown")
    return f"message_id={event.message.id} sender_id={sender_id}"


async def process_file(
    file_path: Path,
    message_context: str,
    store: DailyStore,
    summarizer: GeminiSummarizer,
    zone: ZoneInfo | None,
    target_time: dt_time,
) -> None:
    logger.info("Processing %s from %s", file_path.name, message_context)
    suffix = file_path.suffix.lower()
    if suffix == ".pdf":
        text = await asyncio.to_thread(extract_pdf_text, file_path)
    elif suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        text = await asyncio.to_thread(extract_image_text, file_path)
    else:
        logger.info("Skipping unsupported file type: %s", file_path.name)
        return

    if not text.strip():
        logger.info("No text extracted from %s", file_path.name)
        return

    summary = await asyncio.to_thread(
        summarizer.summarize_text,
        text,
        f"Source file: {file_path.name}",
    )
    if not summary:
        logger.warning("Gemini returned an empty summary for %s", file_path.name)
        return

    bucket_date = active_bucket_date(current_time(zone), target_time)
    await store.add_summary(summary, bucket_date)
    logger.info("Stored summary for %s", file_path.name)


async def worker_loop(
    queue: asyncio.Queue[tuple[Path, str]],
    store: DailyStore,
    summarizer: GeminiSummarizer,
    zone: ZoneInfo | None,
    target_time: dt_time,
) -> None:
    while True:
        file_path, message_context = await queue.get()
        try:
            await process_file(
                file_path,
                message_context,
                store,
                summarizer,
                zone,
                target_time,
            )
        except Exception:
            logger.exception("Failed to process %s", file_path)
        finally:
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError as exc:
                logger.warning("Could not remove %s: %s", file_path, exc)
            queue.task_done()


async def scheduler_loop(
    settings: Settings,
    store: DailyStore,
    summarizer: GeminiSummarizer,
    zone: ZoneInfo | None,
) -> None:
    target_time = parse_daily_time(settings.summary_time)
    while True:
        now = current_time(zone)
        run_at = next_run_after(now, target_time)
        sleep_seconds = max(1, int((run_at - now).total_seconds()))
        logger.info("Next daily summary scheduled for %s", run_at.isoformat())
        await asyncio.sleep(sleep_seconds)

        today = current_time(zone).date()
        if await store.summary_already_sent(today):
            continue

        summaries = await store.get_summaries(today)
        if not summaries:
            logger.info("No summaries collected for %s", today.isoformat())
            await store.mark_sent(today)
            continue

        try:
            final_summary = await asyncio.to_thread(
                summarizer.summarize_many,
                summaries,
                f"Daily digest for {today.isoformat()}",
            )
            message = (
                f"Daily Summary ({today.isoformat()})\n\n{final_summary}"
                if final_summary
                else f"Daily Summary ({today.isoformat()})\n\nNo content to send."
            )
            await asyncio.to_thread(
                send_to_telegram,
                settings.bot_token,
                settings.chat_id,
                message,
            )
            await store.mark_sent(today)
            logger.info("Daily summary sent for %s", today.isoformat())
        except Exception:
            logger.exception("Failed to send daily summary for %s", today.isoformat())


async def main() -> None:
    settings = load_settings()
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    zone = resolve_zone(settings.timezone_name)
    target_time = parse_daily_time(settings.summary_time)
    store = DailyStore(settings.state_file)
    summarizer = GeminiSummarizer(
        api_key=settings.gemini_api_key,
        model_name=settings.model_name,
        max_input_chars=settings.max_input_chars,
    )
    queue: asyncio.Queue[tuple[Path, str]] = asyncio.Queue()

    client = TelegramClient(
        settings.session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    @client.on(events.NewMessage(chats=settings.group))
    async def handler(event: events.NewMessage.Event) -> None:
        if not event.message.media:
            return

        file_name = getattr(event.file, "name", None) or f"{event.message.id}"
        download_path = settings.downloads_dir / file_name
        try:
            saved_to = await event.message.download_media(file=download_path)
            if not saved_to:
                logger.info("Telegram returned no download path for %s", file_name)
                return
            await queue.put((Path(saved_to), describe_message(event)))
            logger.info("Queued %s from %s", file_name, describe_message(event))
        except Exception:
            logger.exception("Failed to download media from %s", describe_message(event))

    worker_task = asyncio.create_task(
        worker_loop(queue, store, summarizer, zone, target_time)
    )
    scheduler_task = asyncio.create_task(
        scheduler_loop(settings, store, summarizer, zone)
    )

    try:
        await client.start()
        logger.info("Telegram client started. Listening to %s", settings.group)
        await client.run_until_disconnected()
    finally:
        worker_task.cancel()
        scheduler_task.cancel()
        await asyncio.gather(worker_task, scheduler_task, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down on keyboard interrupt")
