from datetime import date, datetime, timezone
import unittest

from telegram_summarizer.config import (
    SettingsError,
    active_bucket_date,
    load_settings,
    parse_daily_time,
)


class ConfigTests(unittest.TestCase):
    def test_load_settings_parses_new_defaults(self) -> None:
        settings = load_settings(
            {
                "TELEGRAM_API_ID": "123",
                "TELEGRAM_API_HASH": "hash",
                "GROUP_USERNAME": "-10012345",
                "GEMINI_API_KEY": "key",
                "BOT_TOKEN": "token",
                "CHAT_ID": "chat",
            }
        )
        self.assertEqual(settings.database_path.name, "bot.db")
        self.assertEqual(settings.worker_concurrency, 1)
        self.assertEqual(settings.max_retries, 3)
        self.assertEqual(settings.max_file_mb, 20)
        self.assertEqual(settings.max_pdf_pages, 50)
        self.assertEqual(settings.group, -10012345)

    def test_load_settings_requires_env(self) -> None:
        with self.assertRaises(SettingsError):
            load_settings({})

    def test_active_bucket_rolls_after_summary_time(self) -> None:
        target = parse_daily_time("22:00")
        before = datetime(2026, 5, 3, 21, 59, tzinfo=timezone.utc)
        after = datetime(2026, 5, 3, 22, 1, tzinfo=timezone.utc)
        self.assertEqual(active_bucket_date(before, target), date(2026, 5, 3))
        self.assertEqual(active_bucket_date(after, target), date(2026, 5, 4))
