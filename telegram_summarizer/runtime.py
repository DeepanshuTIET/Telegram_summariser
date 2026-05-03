from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import pytesseract
from dotenv import load_dotenv
from telethon import TelegramClient, events

from .config import Settings, current_time, load_settings, parse_daily_time, resolve_zone
from .extraction import ContentExtractor
from .models import SourceMessage
from .service import QueuedDownload, SummaryService, TelegramBotSender, worker_loop
from .storage import SQLiteStore
from .summarizer import GeminiStructuredSummarizer
from .utils import make_download_path, safe_filename

logger = logging.getLogger("telegram-summary-bot")


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down on keyboard interrupt")


async def main() -> None:
    load_dotenv()
    settings = load_settings()
    configure_logging(settings.log_level)

    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

    zone = resolve_zone(settings.timezone_name)
    summary_time = parse_daily_time(settings.summary_time)

    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    _assert_writable_directory(settings.downloads_dir)

    store = SQLiteStore(settings.database_path)
    store.assert_writable()

    extractor = ContentExtractor(
        max_file_bytes=settings.max_file_bytes,
        max_pdf_pages=settings.max_pdf_pages,
    )
    summarizer = GeminiStructuredSummarizer(
        api_key=settings.gemini_api_key,
        model_name=settings.model_name,
        max_input_chars=settings.max_input_chars,
        max_retries=settings.max_retries,
    )
    sender = TelegramBotSender(
        bot_token=settings.bot_token,
        chat_id=settings.chat_id,
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    service = SummaryService(
        settings=settings,
        zone=zone,
        summary_time=summary_time,
        store=store,
        extractor=extractor,
        summarizer=summarizer,
        sender=sender,
    )

    await _run_startup_checks(settings, extractor, summarizer)

    queue: asyncio.Queue[QueuedDownload] = asyncio.Queue()
    client = TelegramClient(
        settings.session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.start()
    me = await client.get_me()
    await client.get_entity(settings.group)
    logger.info(
        "Telegram auth verified for %s. Listening to %s",
        getattr(me, "username", None) or getattr(me, "id", "unknown"),
        settings.group,
    )

    for recovered in service.recover_pending_downloads():
        await queue.put(recovered)
        logger.info("Recovered unfinished item %s from previous run", recovered.item_id)

    @client.on(events.NewMessage(chats=settings.group))
    async def handler(event: events.NewMessage.Event) -> None:
        if not event.message.media:
            return

        original_name = getattr(event.file, "name", None) or f"{event.message.id}.bin"
        file_size = getattr(event.file, "size", None)
        message = SourceMessage(
            source_chat=str(event.chat_id or settings.group),
            message_id=event.message.id,
            file_name=safe_filename(original_name),
            file_size_bytes=file_size,
        )
        item_id = service.create_item_for_message(message, current_time(zone))
        if item_id is None:
            logger.info(
                "Skipping duplicate Telegram message event for message_id=%s",
                message.message_id,
            )
            return

        if file_size and file_size > settings.max_file_bytes:
            service.skip_item(
                item_id,
                f"File exceeds size limit of {settings.max_file_mb} MB before download.",
            )
            return

        download_path = make_download_path(
            settings.downloads_dir,
            event.message.id,
            original_name,
        )
        try:
            saved_to = await event.message.download_media(file=download_path)
            if not saved_to:
                store.mark_failed(item_id, "Telegram download returned no local file path.")
                return

            saved_path = Path(saved_to)
            should_process = service.register_download(item_id, saved_path)
            if should_process:
                await queue.put(QueuedDownload(item_id=item_id, file_path=saved_path))
                logger.info("Queued downloaded media for item %s", item_id)
            else:
                if saved_path.exists():
                    saved_path.unlink()
                logger.info("Marked item %s as duplicate based on file hash", item_id)
        except Exception as exc:
            store.record_retry(item_id, str(exc))
            store.mark_failed(item_id, f"Download failed: {exc}")
            if download_path.exists():
                try:
                    download_path.unlink()
                except OSError:
                    pass
            logger.exception("Failed to download media for item %s", item_id)

    worker_tasks = [
        asyncio.create_task(
            worker_loop(
                service=service,
                queue=queue,
                worker_name=f"worker-{index + 1}",
            )
        )
        for index in range(settings.worker_concurrency)
    ]
    scheduler_task = asyncio.create_task(
        scheduler_loop(service=service, zone=zone, summary_time=summary_time)
    )

    try:
        logger.info("Worker started successfully.")
        await client.run_until_disconnected()
    finally:
        scheduler_task.cancel()
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(scheduler_task, *worker_tasks, return_exceptions=True)


async def scheduler_loop(
    *,
    service: SummaryService,
    zone,
    summary_time,
) -> None:
    logger.info("Daily summary scheduler is active for %s", summary_time.strftime("%H:%M"))
    while True:
        now = current_time(zone)
        sent = await service.send_daily_digest_if_due(now)
        if sent:
            logger.info("Daily digest sent for %s", now.date().isoformat())
        await asyncio.sleep(30)


async def _run_startup_checks(
    settings: Settings,
    extractor: ContentExtractor,
    summarizer: GeminiStructuredSummarizer,
) -> None:
    _assert_writable_directory(settings.downloads_dir)
    _assert_writable_file(settings.database_path)
    await asyncio.wait_for(
        asyncio.to_thread(extractor.health_check),
        timeout=settings.request_timeout_seconds,
    )
    await asyncio.wait_for(
        asyncio.to_thread(summarizer.health_check),
        timeout=settings.request_timeout_seconds,
    )


def _assert_writable_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write_test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)


def _assert_writable_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    path.touch()
    path.unlink(missing_ok=True)
