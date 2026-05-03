from __future__ import annotations

import re
import uuid
from pathlib import Path


def chunk_text(text: str, limit: int = 3900) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    chunks: list[str] = []
    current = ""
    for line in stripped.splitlines():
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue

        if current:
            chunks.append(current)
            current = line
        else:
            start = 0
            while start < len(line):
                chunks.append(line[start : start + limit])
                start += limit

    if current:
        chunks.append(current)

    return chunks


def safe_filename(name: str) -> str:
    base_name = Path(name or "file").name
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._")
    return sanitized or "file"


def make_download_path(downloads_dir: Path, message_id: int, original_name: str) -> Path:
    suffix = Path(original_name).suffix or ".bin"
    stem = Path(safe_filename(original_name)).stem or "file"
    unique = uuid.uuid4().hex[:8]
    return downloads_dir / f"{message_id}_{stem}_{unique}{suffix}"


def backoff_seconds(attempt: int) -> int:
    return min(2 ** max(0, attempt - 1), 30)
