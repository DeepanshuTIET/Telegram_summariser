from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any


ALLOWED_CATEGORIES = {
    "market_signal",
    "news",
    "research",
    "macro",
    "admin",
    "unknown",
}
ALLOWED_IMPORTANCE = {"high", "medium", "low"}
ALLOWED_OCR_QUALITY = {"high", "medium", "low"}
ALLOWED_STATUSES = {
    "queued",
    "downloaded",
    "extracted",
    "summarized",
    "sent",
    "skipped",
    "failed",
}

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "category": {"type": "string", "enum": sorted(ALLOWED_CATEGORIES)},
        "importance": {"type": "string", "enum": sorted(ALLOWED_IMPORTANCE)},
        "summary_points": {"type": "array", "items": {"type": "string"}},
        "market_calls": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "ocr_quality": {"type": "string", "enum": sorted(ALLOWED_OCR_QUALITY)},
    },
    "required": [
        "headline",
        "category",
        "importance",
        "summary_points",
        "market_calls",
        "risks",
        "actions",
        "ocr_quality",
    ],
}


class SummaryValidationError(ValueError):
    """Raised when the Gemini summary payload does not match the schema."""


class SkippableItemError(RuntimeError):
    """Raised for expected non-fatal item processing failures."""


@dataclass(frozen=True)
class SourceMessage:
    source_chat: str
    message_id: int
    file_name: str
    file_size_bytes: int | None = None


@dataclass(frozen=True)
class ExtractedContent:
    text: str
    ocr_quality: str
    source_type: str
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StructuredSummary:
    headline: str
    category: str
    importance: str
    summary_points: list[str]
    market_calls: list[str]
    risks: list[str]
    actions: list[str]
    ocr_quality: str

    @classmethod
    def from_payload(cls, payload: Any) -> "StructuredSummary":
        if not isinstance(payload, dict):
            raise SummaryValidationError("Gemini response was not a JSON object.")

        headline = _validate_non_empty_str(payload, "headline")
        category = _validate_enum(payload, "category", ALLOWED_CATEGORIES)
        importance = _validate_enum(payload, "importance", ALLOWED_IMPORTANCE)
        summary_points = _validate_string_list(payload, "summary_points", required=True)
        market_calls = _validate_string_list(payload, "market_calls")
        risks = _validate_string_list(payload, "risks")
        actions = _validate_string_list(payload, "actions")
        ocr_quality = _validate_enum(payload, "ocr_quality", ALLOWED_OCR_QUALITY)
        return cls(
            headline=headline,
            category=category,
            importance=importance,
            summary_points=summary_points,
            market_calls=market_calls,
            risks=risks,
            actions=actions,
            ocr_quality=ocr_quality,
        )

    @classmethod
    def from_json(cls, value: str) -> "StructuredSummary":
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SummaryValidationError(f"Summary JSON was invalid: {exc}") from exc
        return cls.from_payload(payload)

    def to_payload(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "category": self.category,
            "importance": self.importance,
            "summary_points": self.summary_points,
            "market_calls": self.market_calls,
            "risks": self.risks,
            "actions": self.actions,
            "ocr_quality": self.ocr_quality,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), ensure_ascii=True)

    def with_ocr_quality(self, value: str) -> "StructuredSummary":
        if value not in ALLOWED_OCR_QUALITY:
            raise SummaryValidationError(f"Invalid OCR quality override: {value}")
        return replace(self, ocr_quality=value)


@dataclass(frozen=True)
class StoredItem:
    id: int
    bucket_date: str
    source_chat: str
    message_id: int
    source_filename: str
    file_path: str | None
    file_size_bytes: int | None
    file_hash: str | None
    status: str
    is_duplicate: bool
    duplicate_of_item_id: int | None
    retry_count: int
    last_error: str | None
    ocr_quality: str | None
    summary: StructuredSummary | None
    created_at: str
    updated_at: str
    sent_at: str | None


def _validate_non_empty_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SummaryValidationError(f"Field '{key}' must be a non-empty string.")
    return value.strip()


def _validate_enum(payload: dict[str, Any], key: str, allowed: set[str]) -> str:
    value = _validate_non_empty_str(payload, key)
    if value not in allowed:
        raise SummaryValidationError(
            f"Field '{key}' must be one of {sorted(allowed)}, got '{value}'."
        )
    return value


def _validate_string_list(
    payload: dict[str, Any], key: str, required: bool = False
) -> list[str]:
    value = payload.get(key)
    if value is None:
        if required:
            raise SummaryValidationError(f"Field '{key}' is required.")
        return []
    if not isinstance(value, list):
        raise SummaryValidationError(f"Field '{key}' must be a list of strings.")
    cleaned = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SummaryValidationError(
                f"Field '{key}[{index}]' must be a non-empty string."
            )
        cleaned.append(item.strip())
    if required and not cleaned:
        raise SummaryValidationError(f"Field '{key}' must contain at least one item.")
    return cleaned
