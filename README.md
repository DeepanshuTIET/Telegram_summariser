# Manager-Ready Telegram Summarizer

Turn Telegram PDFs and screenshots into a structured daily brief that a manager can actually skim and trust.

This worker listens to one Telegram group or channel, downloads supported media, extracts text from PDFs and images, summarizes each item with Gemini into validated JSON, stores processing state in SQLite, and sends a fixed-format daily digest to a Telegram bot chat.

It is intentionally built for long-running workers or VMs, not serverless functions.

## Why This Exists

Teams often receive a mix of trading notes, research screenshots, macro updates, and admin PDFs in Telegram. Important signals get buried, duplicate files waste attention, and unreadable uploads disappear into manual follow-up.

This project solves that by:

- converting raw uploads into structured summaries
- preserving explicit market calls without inventing new ones
- surfacing risks and recommended actions
- reporting what was skipped, duplicated, failed, or still in flight
- keeping durable state so restarts do not silently lose the day

## What The Manager Sees

Every daily message uses the same sections:

1. `Executive Summary`
2. `High-Priority Signals`
3. `Key Risks`
4. `Recommended Actions`
5. `Coverage Report`

That makes the output useful even on messy days because the digest shows both insight quality and operational coverage.

## Key Capabilities

- Telegram listener using Telethon
- PDF text extraction with PyMuPDF
- OCR for images and image-only PDF pages with Tesseract
- Structured Gemini summaries using a JSON schema
- SQLite-backed state tracking and digest history
- Duplicate detection using Telegram message identity and file hashes
- Retry and backoff for processing and Telegram delivery
- Restart recovery for unfinished downloaded items
- Configurable limits for file size, page count, retries, and concurrency

## Reliability Design

This repo is designed to fail clearly instead of failing silently.

### Startup checks

Before the worker begins listening, it verifies:

- required environment variables
- timezone parsing
- writable download directory
- writable SQLite database path
- Tesseract availability
- Gemini connectivity
- Telegram authentication
- Telegram source entity lookup

If one of these checks fails, the process exits with a direct actionable error.

### Processing states

Each item moves through explicit statuses in SQLite:

- `queued`
- `downloaded`
- `extracted`
- `summarized`
- `sent`
- `skipped`
- `failed`

This makes it easy to reason about what happened to every upload.

### Structured summary contract

Each successful Gemini summary is stored as validated JSON with:

- `headline`
- `category`
- `importance`
- `summary_points`
- `market_calls`
- `risks`
- `actions`
- `ocr_quality`

Allowed enums:

- `category`: `market_signal`, `news`, `research`, `macro`, `admin`, `unknown`
- `importance`: `high`, `medium`, `low`
- `ocr_quality`: `high`, `medium`, `low`

## Architecture

### High-level flow

1. Telethon receives a new media message from the configured source.
2. The worker creates an item record in SQLite for the correct daily bucket.
3. The file is downloaded into a unique path under `downloads/`.
4. A SHA-256 hash is computed and checked for duplicates within the day.
5. Supported files are extracted:
   - PDFs use direct text extraction first
   - image-only PDF pages fall back to OCR
   - images go through OCR
6. Gemini produces a schema-constrained summary.
7. The structured result is stored in SQLite.
8. At the scheduled time, the worker builds the final digest from stored summaries and sends it via Telegram Bot API.

### Module layout

- `app.py`
  Thin bootstrap. Starts the runtime only.
- `telegram_summarizer/config.py`
  Loads env vars, validates settings, handles summary-time and daily-bucket logic.
- `telegram_summarizer/runtime.py`
  Runs startup checks, creates the Telegram client, starts workers, handles event flow.
- `telegram_summarizer/service.py`
  Coordinates downloads, extraction, summarization, retries, digest sending, and recovery behavior.
- `telegram_summarizer/storage.py`
  SQLite schema, item state transitions, dedupe history, and digest sent tracking.
- `telegram_summarizer/extraction.py`
  File hashing, OCR, PDF extraction, size/page guardrails, OCR quality estimation.
- `telegram_summarizer/summarizer.py`
  Gemini integration with strict JSON schema validation.
- `telegram_summarizer/digest.py`
  Deterministic manager-friendly digest renderer.
- `tests/`
  Unit and pipeline-style tests for config, schema validation, digest output, retries, duplicates, and restart behavior.

## Repository Structure

```text
.
|-- app.py
|-- .env.example
|-- DEMO_NOTES.md
|-- README.md
|-- requirements.txt
|-- telegram_summarizer/
|   |-- config.py
|   |-- digest.py
|   |-- extraction.py
|   |-- runtime.py
|   |-- service.py
|   |-- storage.py
|   `-- summarizer.py
`-- tests/
```

## Requirements

- Python 3.10 or newer
- Tesseract OCR installed on the host
- A Telegram API ID and API hash for Telethon
- A Telegram bot token for outbound digest messages
- A Gemini API key
- A persistent environment such as Render worker, Railway worker, Oracle Cloud VM, VPS, or an always-on machine

## Installation

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Tesseract OCR

Tesseract must be available on PATH, or you must set `TESSERACT_CMD`.

Example on Windows:

```env
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

Example on Linux:

```env
TESSERACT_CMD=/usr/bin/tesseract
```

### 3. Create your environment file

Copy `.env.example` to `.env` and fill in the values.

## Configuration

### Required variables

| Variable | Description |
|---|---|
| `TELEGRAM_API_ID` | Telegram API ID used by Telethon |
| `TELEGRAM_API_HASH` | Telegram API hash used by Telethon |
| `GROUP_USERNAME` | Source group or channel username, ID, or chat ID |
| `GEMINI_API_KEY` | Gemini API key |
| `BOT_TOKEN` | Telegram bot token used to send digests |
| `CHAT_ID` | Destination chat ID for the digest |

### Optional variables

| Variable | Default | Description |
|---|---:|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model used for structured summaries |
| `SESSION_NAME` | `session` | Local Telethon session file prefix |
| `DOWNLOADS_DIR` | `downloads` | Temporary media download directory |
| `DATABASE_PATH` | `bot.db` | SQLite database path |
| `DAILY_SUMMARY_TIME` | `22:00` | Daily send time in `HH:MM` format |
| `TIMEZONE` | `Asia/Kolkata` | IANA timezone used for scheduling and bucket rollover |
| `MAX_MODEL_INPUT_CHARS` | `15000` | Maximum extracted text passed to Gemini |
| `WORKER_CONCURRENCY` | `1` | Number of parallel processing workers |
| `MAX_RETRIES` | `3` | Retry attempts for processing and sending |
| `REQUEST_TIMEOUT_SECONDS` | `30` | Timeout used around external calls and threaded work |
| `MAX_FILE_MB` | `20` | Max file size accepted for download and extraction |
| `MAX_PDF_PAGES` | `50` | Max number of PDF pages analyzed |
| `TESSERACT_CMD` | empty | Explicit path to Tesseract if not on PATH |
| `LOG_LEVEL` | `INFO` | Logging level |

### Example `.env`

```env
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your_api_hash
GROUP_USERNAME=your_group_username_or_id

GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash

BOT_TOKEN=your_bot_token
CHAT_ID=your_personal_chat_id

SESSION_NAME=session
DOWNLOADS_DIR=downloads
DATABASE_PATH=bot.db
DAILY_SUMMARY_TIME=22:00
TIMEZONE=Asia/Kolkata
MAX_MODEL_INPUT_CHARS=15000
WORKER_CONCURRENCY=1
MAX_RETRIES=3
REQUEST_TIMEOUT_SECONDS=30
MAX_FILE_MB=20
MAX_PDF_PAGES=50
TESSERACT_CMD=
LOG_LEVEL=INFO
```

## Running Locally

Start the worker with:

```bash
python app.py
```

### First run behavior

- Telethon may prompt for your phone login and verification flow.
- A local session file will be created.
- Startup checks run before the worker enters the listening loop.

### What happens during runtime

- New Telegram media is downloaded into `downloads/`
- Supported file types are processed
- Structured summaries are persisted in SQLite
- The daily digest is sent at `DAILY_SUMMARY_TIME`
- Downloaded media is cleaned up after processing

## Output Format

The final digest is deterministic and always ordered the same way.

Example:

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

## Testing

Run the automated test suite:

```bash
python -m unittest discover -s tests -v
```

The test suite covers:

- environment parsing and defaults
- summary-time bucket rollover
- Telegram chunking
- schema validation
- digest formatting
- duplicate handling
- unsupported file skipping
- unreadable OCR skipping
- Gemini failure behavior
- Telegram send retry behavior
- restart-safe digest handling

## Deployment Guidance

This app needs a persistent worker process. Good fits:

- Render background worker
- Railway worker
- Oracle Cloud free VM
- VPS
- always-on local or office machine

### Why not Vercel

This workload is a poor fit for stateless serverless platforms because it needs:

- a long-running Telegram listener
- local filesystem access for downloaded files
- scheduled daily processing
- durable session and state handling

## Operational Notes

### Files created at runtime

- Telethon session files such as `session.session`
- SQLite database such as `bot.db`
- temporary downloads under `downloads/`

These are intentionally excluded by `.gitignore`.

### Duplicate handling

- Telegram message IDs prevent re-processing the same message event
- file hashes prevent summarizing the same uploaded content multiple times in the same day

### Restart recovery

- items already downloaded but not fully processed are recovered on restart
- stale queued items without a completed download are marked failed with a visible reason

## Troubleshooting

### Tesseract not found

Set `TESSERACT_CMD` explicitly and verify the executable exists.

### Gemini health check fails at startup

Check:

- `GEMINI_API_KEY`
- outbound network access
- Gemini model name

### Telegram auth or source lookup fails

Check:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `GROUP_USERNAME`
- whether the authenticated Telegram account can access that group or channel

### Files are skipped often

Check:

- whether uploads exceed `MAX_FILE_MB`
- whether PDFs are image-heavy and OCR quality is low
- whether `MAX_PDF_PAGES` is truncating important pages

## Security Notes

- Do not commit `.env`
- Do not commit Telethon session files
- Treat `bot.db` as local runtime state, not source code
- If you are presenting this repo, redact any real phone numbers, bot tokens, or API keys

## Known Limitations

- OCR quality still depends on the quality of the source image or scan
- This is optimized for reliability and explainability, not high-throughput ingestion
- The digest is only as good as the extracted text and the source material
- Finance-aware summarization preserves explicit calls, but it should not be treated as autonomous trading advice

## Related Docs

- [DEMO_NOTES.md](./DEMO_NOTES.md) for a manager-facing talk track and example demo flow
- [.env.example](./.env.example) for the current configuration template
