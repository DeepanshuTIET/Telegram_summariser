import unittest

from telegram_summarizer.models import StructuredSummary, SummaryValidationError
from telegram_summarizer.utils import chunk_text


class SchemaAndUtilsTests(unittest.TestCase):
    def test_schema_validation_rejects_missing_fields(self) -> None:
        with self.assertRaises(SummaryValidationError):
            StructuredSummary.from_payload({"headline": "Missing everything else"})

    def test_chunk_text_splits_long_messages(self) -> None:
        chunks = chunk_text("A" * 5000, limit=3900)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(sum(len(chunk) for chunk in chunks), 5000)
