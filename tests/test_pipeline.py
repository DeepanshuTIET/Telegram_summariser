from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from telegram_summarizer.config import Settings, parse_daily_time
from telegram_summarizer.models import (
    ExtractedContent,
    SkippableItemError,
    SourceMessage,
    StructuredSummary,
)
from telegram_summarizer.service import SummaryService, TelegramBotSender
from telegram_summarizer.storage import SQLiteStore
from telegram_summarizer.summarizer import GeminiStructuredSummarizer, GeminiSummaryError


class FakeExtractor:
    def __init__(self, *, content: ExtractedContent | None = None, error: Exception | None = None):
        self.content = content
        self.error = error

    def compute_file_hash(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def extract(self, path: Path) -> ExtractedContent:
        if self.error:
            raise self.error
        assert self.content is not None
        return self.content


class FakeSummarizer:
    def __init__(self, *, summary: StructuredSummary | None = None, error: Exception | None = None):
        self.summary = summary
        self.error = error

    def summarize(self, extracted: ExtractedContent, source_name: str) -> StructuredSummary:
        if self.error:
            raise self.error
        assert self.summary is not None
        return self.summary


class FlakySender:
    def __init__(self, failures_before_success: int):
        self.failures_before_success = failures_before_success
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        if self.failures_before_success > 0:
            self.failures_before_success -= 1
            raise RuntimeError("temporary telegram error")
        self.messages.append(text)


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.parsed = None


class FakeGeminiModels:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    def generate_content(self, **kwargs):
        text = self.responses[self.calls]
        self.calls += 1
        return FakeResponse(text)


class FakeGeminiClient:
    def __init__(self, responses: list[str]):
        self.models = FakeGeminiModels(responses)


def make_settings(temp_dir: Path) -> Settings:
    return Settings(
        telegram_api_id=123,
        telegram_api_hash="hash",
        group="group",
        gemini_api_key="key",
        bot_token="token",
        chat_id="chat",
        session_name="session",
        downloads_dir=temp_dir / "downloads",
        database_path=temp_dir / "bot.db",
        model_name="gemini-2.5-flash",
        summary_time="22:00",
        timezone_name="Asia/Kolkata",
        max_input_chars=15000,
        tesseract_cmd=None,
        worker_concurrency=1,
        max_retries=3,
        request_timeout_seconds=5,
        max_file_mb=20,
        max_pdf_pages=50,
        log_level="INFO",
    )


def make_summary() -> StructuredSummary:
    return StructuredSummary(
        headline="Morning market notes",
        category="market_signal",
        importance="high",
        summary_points=["Nifty is near a key level."],
        market_calls=["Watch 22600 for a breakout confirmation."],
        risks=["Momentum can fail on weak breadth."],
        actions=["Share the breakout level with the team."],
        ocr_quality="high",
    )


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir_context = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_context.name)
        self.settings = make_settings(self.temp_dir)
        self.settings.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.store = SQLiteStore(self.settings.database_path)
        self.store.initialize()
        self.zone = timezone.utc
        self.summary_time = parse_daily_time(self.settings.summary_time)

    async def asyncTearDown(self) -> None:
        self.temp_dir_context.cleanup()

    def make_service(
        self,
        *,
        extractor,
        summarizer,
        sender,
    ) -> SummaryService:
        return SummaryService(
            settings=self.settings,
            zone=self.zone,
            summary_time=self.summary_time,
            store=self.store,
            extractor=extractor,
            summarizer=summarizer,
            sender=sender,
        )

    def create_item(self, filename: str, now: datetime | None = None) -> int:
        service = self.make_service(
            extractor=FakeExtractor(content=ExtractedContent(text="x", ocr_quality="high", source_type="image")),
            summarizer=FakeSummarizer(summary=make_summary()),
            sender=FlakySender(0),
        )
        item_id = service.create_item_for_message(
            SourceMessage(source_chat="chat", message_id=hash(filename) % 1000000, file_name=filename),
            now or datetime(2026, 5, 3, 12, 0, tzinfo=self.zone),
        )
        assert item_id is not None
        return item_id

    async def test_duplicate_media_is_skipped(self) -> None:
        service = self.make_service(
            extractor=FakeExtractor(content=ExtractedContent(text="hello", ocr_quality="high", source_type="image")),
            summarizer=FakeSummarizer(summary=make_summary()),
            sender=FlakySender(0),
        )
        item1 = self.create_item("first.png")
        file1 = self.settings.downloads_dir / "1.txt"
        file1.write_text("same-hash", encoding="utf-8")
        self.assertTrue(service.register_download(item1, file1))

        item2 = self.create_item("second.png")
        file2 = self.settings.downloads_dir / "2.txt"
        file2.write_text("same-hash", encoding="utf-8")
        self.assertFalse(service.register_download(item2, file2))
        self.assertTrue(self.store.get_item(item2).is_duplicate)

    async def test_unsupported_files_are_skipped(self) -> None:
        service = self.make_service(
            extractor=FakeExtractor(error=SkippableItemError("Unsupported file type: .txt")),
            summarizer=FakeSummarizer(summary=make_summary()),
            sender=FlakySender(0),
        )
        item_id = self.create_item("notes.txt")
        path = self.settings.downloads_dir / "notes.txt"
        path.write_text("plain text", encoding="utf-8")
        self.store.mark_downloaded(item_id, file_path=path, file_hash="hash-a", file_size_bytes=path.stat().st_size)
        await service.process_item(item_id, path)
        self.assertEqual(self.store.get_item(item_id).status, "skipped")

    async def test_empty_ocr_is_skipped(self) -> None:
        service = self.make_service(
            extractor=FakeExtractor(error=SkippableItemError("No readable text was found in this image.")),
            summarizer=FakeSummarizer(summary=make_summary()),
            sender=FlakySender(0),
        )
        item_id = self.create_item("scan.png")
        path = self.settings.downloads_dir / "scan.png"
        path.write_text("x", encoding="utf-8")
        self.store.mark_downloaded(item_id, file_path=path, file_hash="hash-b", file_size_bytes=path.stat().st_size)
        await service.process_item(item_id, path)
        self.assertEqual(self.store.get_item(item_id).status, "skipped")

    async def test_hard_gemini_failure_marks_item_failed(self) -> None:
        service = self.make_service(
            extractor=FakeExtractor(content=ExtractedContent(text="hello", ocr_quality="high", source_type="image")),
            summarizer=FakeSummarizer(error=GeminiSummaryError("gemini failed")),
            sender=FlakySender(0),
        )
        item_id = self.create_item("market.png")
        path = self.settings.downloads_dir / "market.png"
        path.write_text("payload", encoding="utf-8")
        self.store.mark_downloaded(item_id, file_path=path, file_hash="hash-c", file_size_bytes=path.stat().st_size)
        await service.process_item(item_id, path)
        item = self.store.get_item(item_id)
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.retry_count, self.settings.max_retries)

    async def test_telegram_send_retry_succeeds(self) -> None:
        sender = FlakySender(1)
        service = self.make_service(
            extractor=FakeExtractor(content=ExtractedContent(text="hello", ocr_quality="high", source_type="image")),
            summarizer=FakeSummarizer(summary=make_summary()),
            sender=sender,
        )
        item_id = self.create_item("report.png")
        self.store.mark_summarized(item_id, make_summary())
        sent = await service.send_daily_digest_if_due(
            datetime(2026, 5, 3, 22, 5, tzinfo=self.zone)
        )
        self.assertTrue(sent)
        self.assertEqual(len(sender.messages), 1)

    async def test_restart_recovery_avoids_duplicate_digests(self) -> None:
        sender = FlakySender(0)
        service = self.make_service(
            extractor=FakeExtractor(content=ExtractedContent(text="hello", ocr_quality="high", source_type="image")),
            summarizer=FakeSummarizer(summary=make_summary()),
            sender=sender,
        )
        item_id = self.create_item("daily.png")
        self.store.mark_summarized(item_id, make_summary())
        first = await service.send_daily_digest_if_due(
            datetime(2026, 5, 3, 22, 1, tzinfo=self.zone)
        )
        self.assertTrue(first)

        service_after_restart = self.make_service(
            extractor=FakeExtractor(content=ExtractedContent(text="hello", ocr_quality="high", source_type="image")),
            summarizer=FakeSummarizer(summary=make_summary()),
            sender=sender,
        )
        second = await service_after_restart.send_daily_digest_if_due(
            datetime(2026, 5, 3, 22, 10, tzinfo=self.zone)
        )
        self.assertFalse(second)
        self.assertEqual(len(sender.messages), 1)


class GeminiStructuredSummarizerTests(unittest.TestCase):
    def test_invalid_json_retries_then_succeeds(self) -> None:
        responses = [
            "not-json",
            """
            {
              "headline": "Desk update",
              "category": "market_signal",
              "importance": "high",
              "summary_points": ["Nifty is testing resistance."],
              "market_calls": ["Watch 22600 for confirmation."],
              "risks": ["Breakout may fail on weak breadth."],
              "actions": ["Review intraday confirmation with the team."],
              "ocr_quality": "low"
            }
            """,
        ]
        summarizer = GeminiStructuredSummarizer(
            api_key="key",
            model_name="gemini-2.5-flash",
            max_input_chars=15000,
            max_retries=2,
            client=FakeGeminiClient(responses),
        )
        summary = summarizer.summarize(
            ExtractedContent(
                text="Market note",
                ocr_quality="medium",
                source_type="image",
            ),
            "desk.png",
        )
        self.assertEqual(summary.ocr_quality, "medium")
        self.assertEqual(summary.headline, "Desk update")
