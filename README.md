# Manager-Ready Telegram Summarizer

This project turns noisy Telegram PDFs and images into a reliable, manager-friendly daily digest. It is built for long-running workers, not serverless functions, and is designed to be resilient enough for a demo while still being practical to deploy.

## Business Value

- Converts scattered PDFs and screenshots into one structured daily summary.
- Keeps explicit market or trading calls visible without inventing new ones.
- Surfaces risks, recommended actions, and coverage stats so a manager can quickly see both insight quality and processing health.
- Continues to be useful even on bad-input days by reporting skips, failures, and duplicates clearly.

## Architecture

- `app.py` is a thin bootstrap only.
- `telegram_summarizer/config.py` loads and validates environment configuration.
- `telegram_summarizer/storage.py` uses SQLite for durable item tracking, digest history, dedupe, and restart recovery.
- `telegram_summarizer/extraction.py` handles PDFs, OCR, file hashing, and safety limits.
- `telegram_summarizer/summarizer.py` calls Gemini with a strict JSON schema.
- `telegram_summarizer/digest.py` builds the final daily digest deterministically from structured summaries.
- `telegram_summarizer/runtime.py` owns Telegram listening, startup checks, worker scheduling, and recovery.

## Reliability Safeguards

- Fail-fast startup checks for env vars, writable paths, Tesseract OCR, Gemini connectivity, Telegram auth, and source entity lookup.
- SQLite-backed status tracking with `queued`, `downloaded`, `extracted`, `summarized`, `sent`, `skipped`, and `failed`.
- Duplicate protection using Telegram message identity plus per-day file hashes.
- Retry with backoff for processing and Telegram delivery.
- Restart recovery for unfinished downloaded items.
- Fixed digest sections so the output stays predictable:
  - `Executive Summary`
  - `High-Priority Signals`
  - `Key Risks`
  - `Recommended Actions`
  - `Coverage Report`

## Install

1. Install Python 3.10+.
2. Install Tesseract OCR on the host machine.
3. Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and fill in the required values:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `GROUP_USERNAME`
- `GEMINI_API_KEY`
- `BOT_TOKEN`
- `CHAT_ID`

Important optional settings:

- `DATABASE_PATH` defaults to `bot.db`
- `WORKER_CONCURRENCY` defaults to `1`
- `MAX_RETRIES` defaults to `3`
- `REQUEST_TIMEOUT_SECONDS` defaults to `30`
- `MAX_FILE_MB` defaults to `20`
- `MAX_PDF_PAGES` defaults to `50`
- `DAILY_SUMMARY_TIME` defaults to `22:00`
- `TIMEZONE` defaults to `Asia/Kolkata`
- `TESSERACT_CMD` can be set when `tesseract` is not on PATH

## Run

```bash
python app.py
```

On the first run, Telethon may prompt for Telegram login and create a local session file.

## Test

```bash
python -m unittest discover -s tests -v
```

## Deployment Fit

Use a persistent worker or VM:

- Render background worker
- Railway worker
- Oracle Cloud free VM
- A VPS or always-on machine

Avoid serverless platforms like Vercel for this workload because the bot needs a long-running listener, local downloads, and scheduled background work.

## Known Limits

- OCR quality still depends on source image quality and Tesseract performance.
- The app is optimized for reliability and clarity, not high-throughput ingestion.
- Gemini connectivity is checked at startup, so deployment environments need outbound network access before the worker fully starts.
