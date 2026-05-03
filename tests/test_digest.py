from datetime import date
import unittest

from telegram_summarizer.digest import render_digest
from telegram_summarizer.models import StoredItem, StructuredSummary


def make_item(
    *,
    item_id: int,
    source_filename: str,
    status: str,
    summary: StructuredSummary | None = None,
    last_error: str | None = None,
    is_duplicate: bool = False,
) -> StoredItem:
    return StoredItem(
        id=item_id,
        bucket_date="2026-05-03",
        source_chat="chat",
        message_id=item_id,
        source_filename=source_filename,
        file_path=None,
        file_size_bytes=None,
        file_hash=None,
        status=status,
        is_duplicate=is_duplicate,
        duplicate_of_item_id=None,
        retry_count=0,
        last_error=last_error,
        ocr_quality="high",
        summary=summary,
        created_at="2026-05-03T00:00:00Z",
        updated_at="2026-05-03T00:00:00Z",
        sent_at=None,
    )


class DigestTests(unittest.TestCase):
    def test_digest_contains_fixed_sections(self) -> None:
        summary = StructuredSummary(
            headline="Nifty breakout watch",
            category="market_signal",
            importance="high",
            summary_points=["Index is approaching resistance."],
            market_calls=["Watch breakout above 22600."],
            risks=["Signal fails if volume stays weak."],
            actions=["Review opening range with the trading desk."],
            ocr_quality="high",
        )
        text = render_digest(
            date(2026, 5, 3),
            [
                make_item(item_id=1, source_filename="signal.pdf", status="summarized", summary=summary),
                make_item(item_id=2, source_filename="dup.pdf", status="skipped", last_error="Duplicate file", is_duplicate=True),
            ],
        )
        self.assertIn("Executive Summary", text)
        self.assertIn("High-Priority Signals", text)
        self.assertIn("Key Risks", text)
        self.assertIn("Recommended Actions", text)
        self.assertIn("Coverage Report", text)
        self.assertIn("signal.pdf", text)
        self.assertIn("dup.pdf", text)

    def test_digest_handles_empty_day(self) -> None:
        text = render_digest(date(2026, 5, 3), [])
        self.assertIn("No media items were captured today.", text)
        self.assertIn("Coverage Report", text)
