from __future__ import annotations

from datetime import date

from .models import StoredItem, StructuredSummary


def render_digest(bucket_date: date, items: list[StoredItem]) -> str:
    processed = [item for item in items if item.summary and item.status in {"summarized", "sent"}]
    duplicates = [item for item in items if item.is_duplicate]
    skipped = [
        item for item in items if item.status == "skipped" and not item.is_duplicate
    ]
    failed = [item for item in items if item.status == "failed"]
    in_flight = [
        item for item in items if item.status in {"queued", "downloaded", "extracted"}
    ]

    lines = [f"Daily Summary ({bucket_date.isoformat()})", ""]
    lines.extend(_section("Executive Summary", _build_executive_summary(items, processed, duplicates, skipped, failed)))
    lines.extend(_section("High-Priority Signals", _build_high_priority(processed)))
    lines.extend(_section("Key Risks", _build_risks(processed)))
    lines.extend(_section("Recommended Actions", _build_actions(processed)))
    lines.extend(
        _section(
            "Coverage Report",
            _build_coverage(items, processed, skipped, failed, duplicates, in_flight),
        )
    )
    return "\n".join(lines).strip()


def _section(title: str, bullets: list[str]) -> list[str]:
    section_lines = [title]
    if bullets:
        section_lines.extend(f"- {bullet}" for bullet in bullets)
    else:
        section_lines.append("- No updates.")
    section_lines.append("")
    return section_lines


def _build_executive_summary(
    all_items: list[StoredItem],
    processed: list[StoredItem],
    duplicates: list[StoredItem],
    skipped: list[StoredItem],
    failed: list[StoredItem],
) -> list[str]:
    if not all_items:
        return [
            "No media items were captured today.",
            "The worker is healthy but there was no source activity to summarize.",
        ]

    high_priority = [
        item
        for item in processed
        if item.summary
        and (
            item.summary.importance == "high"
            or item.summary.category == "market_signal"
            or bool(item.summary.market_calls)
        )
    ]
    categories = sorted({item.summary.category for item in processed if item.summary})
    headlines = [
        f"{item.summary.headline} [{item.source_filename}]"
        for item in processed[:3]
        if item.summary
    ]

    bullets = [
        f"Observed {len(all_items)} upload(s): {len(processed)} processed, "
        f"{len(duplicates)} duplicate, {len(skipped)} skipped, {len(failed)} failed.",
        f"High-priority items detected: {len(high_priority)}. Categories covered: "
        f"{', '.join(categories) if categories else 'none'}.",
    ]
    if headlines:
        bullets.append("Top headlines: " + "; ".join(headlines) + ".")
    else:
        bullets.append("No processable content was summarized from today's uploads.")
    return bullets


def _build_high_priority(processed: list[StoredItem]) -> list[str]:
    bullets: list[str] = []
    for item in processed:
        summary = item.summary
        if summary is None:
            continue
        if (
            summary.importance == "high"
            or summary.category == "market_signal"
            or summary.market_calls
        ):
            detail = summary.market_calls[0] if summary.market_calls else summary.summary_points[0]
            bullets.append(f"[{item.source_filename}] {summary.headline}: {detail}")
    if bullets:
        return bullets[:6]
    return ["No explicit high-priority or market signals were detected in today's processed sources."]


def _build_risks(processed: list[StoredItem]) -> list[str]:
    risks: list[str] = []
    for item in processed:
        summary = item.summary
        if summary is None:
            continue
        for risk in summary.risks:
            risks.append(f"[{item.source_filename}] {risk}")
    if risks:
        return _unique_preserve_order(risks)[:8]
    return ["No explicit risks were identified in the processed sources."]


def _build_actions(processed: list[StoredItem]) -> list[str]:
    actions: list[str] = []
    for item in processed:
        summary = item.summary
        if summary is None:
            continue
        for action in summary.actions:
            actions.append(f"[{item.source_filename}] {action}")
    if actions:
        return _unique_preserve_order(actions)[:8]
    return ["No explicit follow-up actions were extracted; manual review is optional."]


def _build_coverage(
    all_items: list[StoredItem],
    processed: list[StoredItem],
    skipped: list[StoredItem],
    failed: list[StoredItem],
    duplicates: list[StoredItem],
    in_flight: list[StoredItem],
) -> list[str]:
    return [
        f"Processed: {len(processed)} ({_format_items(processed, include_reason=False)})",
        f"Skipped: {len(skipped)} ({_format_items(skipped, include_reason=True)})",
        f"Failed: {len(failed)} ({_format_items(failed, include_reason=True)})",
        f"Duplicates: {len(duplicates)} ({_format_items(duplicates, include_reason=True)})",
        f"In-flight: {len(in_flight)} ({_format_items(in_flight, include_reason=False)})",
        f"Total tracked items: {len(all_items)}.",
    ]


def _format_items(items: list[StoredItem], *, include_reason: bool) -> str:
    if not items:
        return "none"
    parts = []
    for item in items:
        if include_reason and item.last_error:
            parts.append(f"{item.source_filename} [{item.last_error}]")
        else:
            parts.append(item.source_filename)
    return ", ".join(parts)


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_sample_summary(
    *, headline: str = "Sample headline", category: str = "news", importance: str = "medium"
) -> StructuredSummary:
    return StructuredSummary(
        headline=headline,
        category=category,
        importance=importance,
        summary_points=["Example summary point"],
        market_calls=[],
        risks=["Example risk"],
        actions=["Example action"],
        ocr_quality="high",
    )
