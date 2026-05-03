# Telegram Media Summarizer

Long-running Telegram listener that:

- watches one Telegram group or channel with Telethon
- downloads PDFs and images
- extracts text with PyMuPDF and Tesseract OCR
- summarizes content with Gemini
- sends a daily summary to your Telegram bot chat

## Install

1. Install Python 3.10+.
2. Install system Tesseract OCR.
3. Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Configure

Copy `.env.example` to `.env` and fill in:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `GROUP_USERNAME`
- `GEMINI_API_KEY`
- `BOT_TOKEN`
- `CHAT_ID`

Optional settings:

- `GEMINI_MODEL` defaults to `gemini-2.5-flash`
- `DAILY_SUMMARY_TIME` defaults to `22:00`
- `TIMEZONE` defaults to `Asia/Kolkata`
- `TESSERACT_CMD` can be set explicitly if `tesseract` is not already on PATH

## Run

```bash
python app.py
```

On the first run, Telethon will prompt for Telegram login and create a local session file.

## Deploy

This app needs a persistent worker, not a serverless function. Good fits:

- Render background worker
- Railway
- Oracle Cloud free VM
- your own always-on machine or VPS

## Notes

- The script persists the current day's summaries in `state.json`, so a restart does not lose progress.
- Empty or OCR-unreadable files are skipped.
- Telegram messages are chunked to stay under message-size limits.
