from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from .models import StoredItem, StructuredSummary


class SQLiteStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.lock = threading.Lock()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket_date TEXT NOT NULL,
                    source_chat TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    source_filename TEXT NOT NULL,
                    file_path TEXT,
                    file_size_bytes INTEGER,
                    file_hash TEXT,
                    status TEXT NOT NULL,
                    is_duplicate INTEGER NOT NULL DEFAULT 0,
                    duplicate_of_item_id INTEGER,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    ocr_quality TEXT,
                    summary_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT,
                    UNIQUE(source_chat, message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_items_bucket_date
                    ON items(bucket_date, status);
                CREATE INDEX IF NOT EXISTS idx_items_file_hash
                    ON items(bucket_date, file_hash);

                CREATE TABLE IF NOT EXISTS digests (
                    bucket_date TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    message_text TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sent_at TEXT
                );
                """
            )

    def assert_writable(self) -> None:
        self.initialize()
        with self._connection() as connection:
            connection.execute("SELECT 1")

    def create_item(
        self,
        *,
        bucket_date: date,
        source_chat: str,
        message_id: int,
        source_filename: str,
        file_size_bytes: int | None,
    ) -> int | None:
        now = self._now()
        with self.lock, self._connection() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO items (
                        bucket_date,
                        source_chat,
                        message_id,
                        source_filename,
                        file_size_bytes,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        bucket_date.isoformat(),
                        source_chat,
                        message_id,
                        source_filename,
                        file_size_bytes,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            return int(cursor.lastrowid)

    def mark_downloaded(
        self,
        item_id: int,
        *,
        file_path: Path,
        file_hash: str,
        file_size_bytes: int,
    ) -> bool:
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT bucket_date FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Item {item_id} was not found in the database.")

            duplicate = connection.execute(
                """
                SELECT id
                FROM items
                WHERE bucket_date = ?
                  AND file_hash = ?
                  AND id != ?
                  AND is_duplicate = 0
                ORDER BY id
                LIMIT 1
                """,
                (row["bucket_date"], file_hash, item_id),
            ).fetchone()

            now = self._now()
            if duplicate is not None:
                connection.execute(
                    """
                    UPDATE items
                    SET file_path = ?,
                        file_size_bytes = ?,
                        file_hash = ?,
                        status = 'skipped',
                        is_duplicate = 1,
                        duplicate_of_item_id = ?,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        str(file_path),
                        file_size_bytes,
                        file_hash,
                        int(duplicate["id"]),
                        f"Duplicate file hash matched item {int(duplicate['id'])}.",
                        now,
                        item_id,
                    ),
                )
                return False

            connection.execute(
                """
                UPDATE items
                SET file_path = ?,
                    file_size_bytes = ?,
                    file_hash = ?,
                    status = 'downloaded',
                    last_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (str(file_path), file_size_bytes, file_hash, now, item_id),
            )
            return True

    def mark_extracted(self, item_id: int, ocr_quality: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE items
                SET status = 'extracted',
                    ocr_quality = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (ocr_quality, self._now(), item_id),
            )

    def mark_summarized(self, item_id: int, summary: StructuredSummary) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE items
                SET status = 'summarized',
                    ocr_quality = ?,
                    summary_json = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (summary.ocr_quality, summary.to_json(), self._now(), item_id),
            )

    def mark_skipped(
        self,
        item_id: int,
        reason: str,
        *,
        is_duplicate: bool = False,
        duplicate_of_item_id: int | None = None,
    ) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE items
                SET status = 'skipped',
                    is_duplicate = ?,
                    duplicate_of_item_id = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if is_duplicate else 0,
                    duplicate_of_item_id,
                    reason,
                    self._now(),
                    item_id,
                ),
            )

    def record_retry(self, item_id: int, error: str) -> int:
        with self.lock, self._connection() as connection:
            current = connection.execute(
                "SELECT retry_count FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if current is None:
                raise RuntimeError(f"Item {item_id} was not found in the database.")
            next_retry = int(current["retry_count"]) + 1
            connection.execute(
                """
                UPDATE items
                SET retry_count = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (next_retry, error, self._now(), item_id),
            )
            return next_retry

    def mark_failed(self, item_id: int, reason: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE items
                SET status = 'failed',
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (reason, self._now(), item_id),
            )

    def get_item(self, item_id: int) -> StoredItem:
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM items WHERE id = ?",
                (item_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Item {item_id} was not found in the database.")
            return self._row_to_item(row)

    def list_items_for_bucket(self, bucket_date: date) -> list[StoredItem]:
        with self.lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM items
                WHERE bucket_date = ?
                ORDER BY id
                """,
                (bucket_date.isoformat(),),
            ).fetchall()
            return [self._row_to_item(row) for row in rows]

    def list_recoverable_items(self) -> list[StoredItem]:
        with self.lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM items
                WHERE status IN ('downloaded', 'extracted')
                ORDER BY id
                """
            ).fetchall()
            return [self._row_to_item(row) for row in rows]

    def mark_stale_queued_items_failed(self) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE items
                SET status = 'failed',
                    last_error = 'App restarted before media download completed.',
                    updated_at = ?
                WHERE status = 'queued'
                """,
                (self._now(),),
            )

    def digest_sent_for(self, bucket_date: date) -> bool:
        with self.lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT status
                FROM digests
                WHERE bucket_date = ?
                """,
                (bucket_date.isoformat(),),
            ).fetchone()
            return bool(row and row["status"] == "sent")

    def record_digest_failure(self, bucket_date: date, message: str, error: str) -> None:
        self._upsert_digest(
            bucket_date=bucket_date,
            status="failed",
            message_text=message,
            last_error=error,
            sent_at=None,
        )

    def record_digest_sent(self, bucket_date: date, message: str) -> None:
        sent_at = self._now()
        self._upsert_digest(
            bucket_date=bucket_date,
            status="sent",
            message_text=message,
            last_error=None,
            sent_at=sent_at,
        )
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE items
                SET status = 'sent',
                    sent_at = ?,
                    updated_at = ?
                WHERE bucket_date = ?
                  AND status = 'summarized'
                """,
                (sent_at, sent_at, bucket_date.isoformat()),
            )

    def _upsert_digest(
        self,
        *,
        bucket_date: date,
        status: str,
        message_text: str,
        last_error: str | None,
        sent_at: str | None,
    ) -> None:
        now = self._now()
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO digests (
                    bucket_date,
                    status,
                    message_text,
                    last_error,
                    created_at,
                    updated_at,
                    sent_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_date) DO UPDATE SET
                    status = excluded.status,
                    message_text = excluded.message_text,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at,
                    sent_at = excluded.sent_at
                """,
                (
                    bucket_date.isoformat(),
                    status,
                    message_text,
                    last_error,
                    now,
                    now,
                    sent_at,
                ),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> StoredItem:
        summary = None
        if row["summary_json"]:
            try:
                summary = StructuredSummary.from_json(row["summary_json"])
            except Exception:
                summary = None
        return StoredItem(
            id=int(row["id"]),
            bucket_date=str(row["bucket_date"]),
            source_chat=str(row["source_chat"]),
            message_id=int(row["message_id"]),
            source_filename=str(row["source_filename"]),
            file_path=row["file_path"],
            file_size_bytes=row["file_size_bytes"],
            file_hash=row["file_hash"],
            status=str(row["status"]),
            is_duplicate=bool(row["is_duplicate"]),
            duplicate_of_item_id=row["duplicate_of_item_id"],
            retry_count=int(row["retry_count"]),
            last_error=row["last_error"],
            ocr_quality=row["ocr_quality"],
            summary=summary,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            sent_at=row["sent_at"],
        )
