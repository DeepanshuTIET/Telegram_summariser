# Demo Notes

## Manager Talk Track

This worker monitors a Telegram source, extracts useful information from PDFs and images, and sends a structured daily digest instead of forcing the team to manually scan every upload. The goal is not only to summarize content, but also to make the process operationally trustworthy: every item is tracked, duplicates are filtered, failures are visible, and the final digest explains both insights and coverage quality.

## What Makes This Demo Stronger

- The output is manager-friendly, not just AI-generated bullets.
- Every processed item is stored with status, retry count, and failure reason.
- Duplicates and unreadable files do not silently disappear.
- The final message always includes a `Coverage Report`, so the manager sees what was summarized and what was not.

## Sample Daily Digest

```text
Daily Summary (2026-05-03)

Executive Summary
- Observed 4 upload(s): 2 processed, 1 duplicate, 0 skipped, 1 failed.
- High-priority items detected: 1. Categories covered: market_signal, news.
- Top headlines: Nifty breakout watch [desk_signal.pdf]; Macro calendar update [macro_note.png].

High-Priority Signals
- [desk_signal.pdf] Nifty breakout watch: Watch 22600 for confirmation.

Key Risks
- [desk_signal.pdf] Breakout may fail on weak breadth.
- [macro_note.png] Event volatility may increase around the US data release.

Recommended Actions
- [desk_signal.pdf] Share the breakout level with the team.
- [macro_note.png] Review exposure ahead of the event window.

Coverage Report
- Processed: 2 (desk_signal.pdf, macro_note.png)
- Skipped: 0 (none)
- Failed: 1 (blurred_chart.jpg [No readable text was found in this image.])
- Duplicates: 1 (desk_signal_copy.pdf [Duplicate file hash matched item 7.])
- In-flight: 0 (none)
- Total tracked items: 4.
```

## Suggested Demo Flow

1. Show the README headline and business value section.
2. Point out the reliability features: startup checks, SQLite state tracking, dedupe, retries, and restart recovery.
3. Walk through the sample digest and highlight that it contains both insight quality and operational coverage.
4. Call out that the system is intentionally designed for a persistent worker or VM, not serverless infrastructure.
