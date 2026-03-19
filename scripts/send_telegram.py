#!/usr/bin/env python3
"""Send formatted marketing intelligence digest via Telegram."""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
PROCESSED_FILE = os.path.join(DATA_DIR, "processed_articles.json")
NEW_FILE = os.path.join(DATA_DIR, "new_articles.json")
MAX_MSG_LEN = 4000
MAX_RETRIES = 2
WAT = timezone(timedelta(hours=1))

SENTIMENT_EMOJI = {
    "Positive": "\u2705",
    "Negative": "\U0001f7e5",
    "Neutral": "\u25aa\ufe0f",
}


def load_processed():
    """Load processed articles from disk."""
    if not os.path.exists(PROCESSED_FILE):
        return []
    try:
        with open(PROCESSED_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def send_message(bot_token, chat_id, text):
    """Send a message via Telegram Bot API with retry logic."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                print(f"  Telegram error (attempt {attempt + 1}): {resp.status_code} - {resp.text}")
            else:
                result = resp.json()
                if result.get("ok"):
                    return True
                print(f"  Telegram API returned ok=false: {result}")
        except Exception as e:
            print(f"  Telegram send error (attempt {attempt + 1}): {e}")
        if attempt < MAX_RETRIES:
            time.sleep(2)
    return False


def format_digest(articles):
    """Format articles into a Telegram HTML digest."""
    now = datetime.now(WAT).strftime("%B %d, %Y \u2022 %I:%M %p WAT")

    header = (
        f"<b>\U0001f4e1 Ad Intel Digest</b>\n"
        f"<i>{now}</i>\n"
        f"<b>{len(articles)} notable update{'s' if len(articles) != 1 else ''}</b>\n"
        f"{'=' * 30}\n"
    )

    footer = "\n\n<i>Powered by Ad Intel Agent | Next update in 3 hours</i>"

    # Group by category
    categories = {}
    for art in articles:
        cat = art.get("category", "Uncategorized")
        categories.setdefault(cat, []).append(art)

    body_parts = []
    for cat in sorted(categories.keys()):
        section = f"\n<b>\U0001f4cc {cat}</b>\n"
        for art in categories[cat]:
            emoji = SENTIMENT_EMOJI.get(art.get("sentiment", "Neutral"), "\u25aa\ufe0f")
            title = art.get("title", "Untitled")
            url = art.get("url", "")
            source = art.get("source", "Unknown")
            summary = art.get("summary", "")
            section += f"{emoji} <a href=\"{url}\"><b>{title}</b></a>\n"
            section += f"    <i>{source}</i>\n"
            if summary:
                section += f"    {summary}\n"
        body_parts.append(section)

    return header, body_parts, footer


def split_messages(header, body_parts, footer):
    """Split content into messages that fit Telegram's character limit."""
    messages = []
    current = header

    for part in body_parts:
        if len(current) + len(part) + len(footer) > MAX_MSG_LEN:
            if current.strip():
                messages.append(current)
            current = ""
        current += part

    current += footer
    if current.strip():
        messages.append(current)

    return messages


def cleanup():
    """Remove temporary article files after successful send."""
    for filepath in [NEW_FILE, PROCESSED_FILE]:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token:
        print("Error: TELEGRAM_BOT_TOKEN environment variable is not set.")
        sys.exit(1)
    if not chat_id:
        print("Error: TELEGRAM_CHAT_ID environment variable is not set.")
        sys.exit(1)

    articles = load_processed()

    if not articles:
        now = datetime.now(WAT).strftime("%B %d, %Y \u2022 %I:%M %p WAT")
        msg = (
            f"<b>\U0001f4e1 Ad Intel Digest</b>\n"
            f"<i>{now}</i>\n\n"
            f"No notable updates this cycle.\n\n"
            f"<i>Powered by Ad Intel Agent | Next update in 3 hours</i>"
        )
        success = send_message(bot_token, chat_id, msg)
        if success:
            print("Sent 'no updates' message to Telegram.")
        else:
            print("Failed to send message to Telegram.")
            sys.exit(1)
        return

    header, body_parts, footer = format_digest(articles)
    messages = split_messages(header, body_parts, footer)

    print(f"Sending {len(messages)} message(s) to Telegram...")
    all_sent = True
    for i, msg in enumerate(messages):
        success = send_message(bot_token, chat_id, msg)
        if success:
            print(f"  Message {i + 1}/{len(messages)} sent.")
        else:
            print(f"  Message {i + 1}/{len(messages)} FAILED.")
            all_sent = False
        if i < len(messages) - 1:
            time.sleep(1)

    if all_sent:
        cleanup()
        print("All messages sent successfully. Cleaned up temp files.")
    else:
        print("Some messages failed. Temp files retained for retry.")
        sys.exit(1)


if __name__ == "__main__":
    main()
