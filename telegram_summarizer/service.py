from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from .config import Settings, active_bucket_date
from .digest import render_digest
from .models import ExtractedContent, SkippableItemError, SourceMessage, StoredItem
from .storage import SQLiteStore
from .utils import backoff_seconds, chunk_text

logger = logging.getLogger("telegram-summary-bot")


@dataclass(frozen=True)
class QueuedDownload:
    item_id: int
    file_path: Path


class TelegramBotSender:
    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        request_timeout_seconds: int,
        post_func: Any | None = None,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.request_timeout_seconds = request_timeout_seconds
        self.post_func = post_func or requests.post

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        for chunk in chunk_text(text, 3900):
            response = self.post_func(
                url,
                data={"chat_id": self.chat_id, "text": chunk},
                timeout=self.request_timeout_seconds,
            )
            response.raise_for_status()


class SummaryService:
    def __init__(
        self,
        *,
        settings: Settings,
        zone: ZoneInfo,
        summary_time: dt_time,
        store: SQLiteStore,
        extractor: Any,
        summarizer: Any,
        sender: TelegramBotSender,
    ):
        self.settings = settings
        self.zone = zone
        self.summary_time = summary_time
        self.store = store
        self.extractor = extractor
        self.summarizer = summarizer
        self.sender = sender

    def create_item_for_message(self, message: SourceMessage, now: datetime) -> int | None:
        bucket_date = active_bucket_date(now, self.summary_time)
        return self.store.create_item(
            bucket_date=bucket_date,
            source_chat=message.source_chat,
            message_id=message.message_id,
            source_filename=message.file_name,
            file_size_bytes=message.file_size_bytes,
        )

    def skip_item(self, item_id: int, reason: str) -> None:
        self.store.mark_skipped(item_id, reason)

    def register_download(self, item_id: int, file_path: Path) -> bool:
        file_hash = self.extractor.compute_file_hash(file_path)
        file_size_bytes = file_path.stat().st_size
        return self.store.mark_downloaded(
            item_id,
            file_path=file_path,
            file_hash=file_hash,
            file_size_bytes=file_size_bytes,
        )

    def recover_pending_downloads(self) -> list[QueuedDownload]:
        self.store.mark_stale_queued_items_failed()
        jobs: list[QueuedDownload] = []
        for item in self.store.list_recoverable_items():
            if item.file_path and Path(item.file_path).exists():
                jobs.append(QueuedDownload(item.id, Path(item.file_path)))
            else:
                self.store.mark_failed(
                    item.id,
                    "Downloaded media was missing during restart recovery.",
                )
        return jobs

    async def process_item(self, item_id: int, file_path: Path) -> None:
        item = self.store.get_item(item_id)
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                extracted = await asyncio.wait_for(
                    asyncio.to_thread(self.extractor.extract, file_path),
                    timeout=self.settings.request_timeout_seconds,
                )
                self.store.mark_extracted(item_id, extracted.ocr_quality)
                summary = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.summarizer.summarize,
                        extracted,
                        item.source_filename,
                    ),
                    timeout=self.settings.request_timeout_seconds,
                )
                self.store.mark_summarized(item_id, summary)
                return
            except SkippableItemError as exc:
                self.store.mark_skipped(item_id, str(exc))
                return
            except Exception as exc:
                retry_count = self.store.record_retry(item_id, str(exc))
                if attempt >= self.settings.max_retries:
                    self.store.mark_failed(
                        item_id,
                        f"Processing failed after {retry_count} attempt(s): {exc}",
                    )
                    logger.exception("Processing failed for item %s", item_id)
                    return
                await asyncio.sleep(backoff_seconds(attempt))

    async def send_daily_digest_if_due(self, now: datetime) -> bool:
        today = now.date()
        if now.time() < self.summary_time:
            return False
        if self.store.digest_sent_for(today):
            return False

        items = self.store.list_items_for_bucket(today)
        message = render_digest(today, items)
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self.sender.send, message),
                    timeout=self.settings.request_timeout_seconds
                    * max(1, len(chunk_text(message))),
                )
                self.store.record_digest_sent(today, message)
                return True
            except Exception as exc:
                self.store.record_digest_failure(today, message, str(exc))
                if attempt >= self.settings.max_retries:
                    logger.exception("Failed to send daily digest for %s", today)
                    return False
                await asyncio.sleep(backoff_seconds(attempt))
        return False

    def describe_item(self, item: StoredItem) -> str:
        return f"item_id={item.id} message_id={item.message_id} file={item.source_filename}"


async def worker_loop(
    *,
    service: SummaryService,
    queue: asyncio.Queue[QueuedDownload],
    worker_name: str,
) -> None:
    while True:
        queued = await queue.get()
        try:
            logger.info("%s processing item %s", worker_name, queued.item_id)
            await service.process_item(queued.item_id, queued.file_path)
        finally:
            try:
                if queued.file_path.exists():
                    queued.file_path.unlink()
            except OSError as exc:
                logger.warning("Could not clean up %s: %s", queued.file_path, exc)
            queue.task_done()
