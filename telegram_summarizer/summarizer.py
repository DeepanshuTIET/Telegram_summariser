from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .models import ExtractedContent, SUMMARY_SCHEMA, StructuredSummary, SummaryValidationError


class GeminiSummaryError(RuntimeError):
    """Raised when Gemini cannot produce a valid structured summary."""


class GeminiStructuredSummarizer:
    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        max_input_chars: int,
        max_retries: int,
        client: Any | None = None,
    ):
        if client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - depends on local env
                raise GeminiSummaryError(
                    "google-genai is not installed. Run 'pip install -r requirements.txt'."
                ) from exc
            client = genai.Client(api_key=api_key)
        self.client = client
        self.model_name = model_name
        self.max_input_chars = max_input_chars
        self.max_retries = max_retries

    def health_check(self) -> None:
        response = self.client.models.generate_content(
            model=self.model_name,
            contents="Return a JSON object with a status field set to ok.",
            config={
                "response_mime_type": "application/json",
                "response_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                },
            },
        )
        payload = self._extract_payload(response)
        if payload.get("status") != "ok":
            raise GeminiSummaryError(
                "Gemini health check returned an unexpected response payload."
            )

    def summarize(self, extracted: ExtractedContent, source_name: str) -> StructuredSummary:
        trimmed_text = extracted.text.strip()[: self.max_input_chars]
        prompt = self._build_prompt(
            text=trimmed_text,
            source_name=source_name,
            extracted=extracted,
        )
        last_error: Exception | None = None
        for _ in range(self.max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": SUMMARY_SCHEMA,
                    },
                )
                payload = self._extract_payload(response)
                summary = StructuredSummary.from_payload(payload)
                return replace(summary, ocr_quality=extracted.ocr_quality)
            except (json.JSONDecodeError, SummaryValidationError, ValueError) as exc:
                last_error = exc
            except Exception as exc:  # pragma: no cover - network/client errors
                last_error = exc

        raise GeminiSummaryError(
            "Gemini could not produce a valid structured summary."
        ) from last_error

    @staticmethod
    def _build_prompt(
        *, text: str, source_name: str, extracted: ExtractedContent
    ) -> str:
        note_block = "\n".join(f"- {note}" for note in extracted.notes) or "- None"
        return (
            "You summarize Telegram-delivered finance and operations content for a "
            "manager-facing daily digest.\n\n"
            "Rules:\n"
            "- Be concise, factual, and useful.\n"
            "- If the source contains explicit market or trading calls, preserve them "
            "clearly in market_calls.\n"
            "- Do not invent BUY/SELL calls, prices, risks, or actions.\n"
            "- Use category values only from the allowed schema.\n"
            "- Use empty arrays when a field has no grounded content.\n"
            "- Make actions practical for a manager or analyst reviewing the source.\n"
            f"- OCR quality hint: {extracted.ocr_quality}.\n\n"
            f"Source filename: {source_name}\n"
            f"Source type: {extracted.source_type}\n"
            f"Truncated during extraction: {'yes' if extracted.truncated else 'no'}\n"
            "Extraction notes:\n"
            f"{note_block}\n\n"
            "Source content:\n"
            f"{text}"
        )

    @staticmethod
    def _extract_payload(response: Any) -> dict[str, Any]:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, dict):
            return parsed

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise SummaryValidationError("Gemini returned an empty response body.")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise SummaryValidationError("Gemini JSON response was not an object.")
        return payload
