#!/usr/bin/env python3
"""Telegram long-polling listener for on-demand commands.

Handles:
  /start, /help                 - welcome + command list
  /digest                       - trigger fresh scan + digest
  /analyze <topic|headline|url> - strategic analysis via Groq
  /sources                      - show configured queries and feeds
  /status                       - show last digest time + counters
  (any other text)              - treated as a shortcut for /analyze
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

import requests


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
OFFSET_FILE = os.path.join(DATA_DIR, "telegram_offset.json")
SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")
SEEN_FILE = os.path.join(DATA_DIR, "seen_articles.json")
LAST_DIGEST_FILE = os.path.join(DATA_DIR, "last_digest.json")

WAT = timezone(timedelta(hours=1))
POLL_TIMEOUT = 0  # short-poll (single pass per cron run)
MAX_RETRIES = 2

ANALYZE_SYSTEM_PROMPT = """You are Joshua's senior Nigerian marketing and advertising strategist. A piece of news, a headline, a topic, or a URL has been sent to you. Analyse it from the perspective of what it means for the Nigerian advertising, marketing, media, and business landscape. Structure your response as:

\U0001f4cd WHAT THIS IS: One-sentence summary.
\U0001f4a1 WHY IT MATTERS: 2-3 sentences on the strategic implications for marketers, agencies, or brands operating in Nigeria.
\U0001f3af WHO SHOULD CARE: Which industry players (agencies, clients, sectors) are most affected.
\U0001f680 OPPORTUNITY OR THREAT: A sharp, specific angle — what someone in the industry should DO with this information.

Be direct. No fluff. No corporate speak. Write like a strategist briefing a client over coffee."""


def load_offset():
    """Load last processed update_id + 1."""
    if os.path.exists(OFFSET_FILE):
        try:
            with open(OFFSET_FILE, "r") as f:
                data = json.load(f)
            return int(data.get("offset", 0))
        except (json.JSONDecodeError, IOError, ValueError):
            pass
    return 0


def save_offset(offset):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OFFSET_FILE, "w") as f:
        json.dump({"offset": int(offset)}, f, indent=2)


def send_message(bot_token, chat_id, text, parse_mode="HTML"):
    """Send a message via Telegram Bot API with retry logic."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code != 200:
                print(f"  Telegram error (attempt {attempt + 1}): {resp.status_code} - {resp.text}")
                # If HTML parse fails, retry as plain text
                if resp.status_code == 400 and parse_mode == "HTML":
                    payload["parse_mode"] = None
                    payload.pop("parse_mode", None)
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


def get_updates(bot_token, offset):
    """Fetch updates since the given offset."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {
        "offset": offset,
        "timeout": POLL_TIMEOUT,
        "allowed_updates": json.dumps(["message"]),
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            print(f"  getUpdates returned ok=false: {data}")
            return []
        return data.get("result", [])
    except Exception as e:
        print(f"  getUpdates error: {e}")
        return []


def handle_start(bot_token, chat_id):
    msg = (
        "<b>\U0001f4e1 Ad Intel Agent</b>\n"
        "Your on-demand Nigerian marketing intelligence bot.\n\n"
        "<b>Commands:</b>\n"
        "• /digest — trigger a fresh scan and digest now\n"
        "• /analyze &lt;topic|headline|URL&gt; — strategic analysis\n"
        "• /sources — list monitored queries and feeds\n"
        "• /status — last digest time and counters\n"
        "• /help — show this menu\n\n"
        "<i>Tip: Paste any headline or link without a command and I'll analyse it.</i>"
    )
    send_message(bot_token, chat_id, msg)


def handle_digest(bot_token, chat_id):
    send_message(bot_token, chat_id, "\U0001f504 Scanning latest news... hold on.")
    scripts = [
        os.path.join(SCRIPTS_DIR, "fetch_feeds.py"),
        os.path.join(SCRIPTS_DIR, "process_with_groq.py"),
        os.path.join(SCRIPTS_DIR, "send_telegram.py"),
    ]
    for script in scripts:
        print(f"  Running {os.path.basename(script)}...")
        try:
            result = subprocess.run(
                [sys.executable, script],
                capture_output=True,
                text=True,
                timeout=900,
                env=os.environ.copy(),
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout or "unknown error")[-500:]
                send_message(
                    bot_token, chat_id,
                    f"❌ <b>{os.path.basename(script)} failed.</b>\n<pre>{err}</pre>",
                )
                return
        except subprocess.TimeoutExpired:
            send_message(
                bot_token, chat_id,
                f"❌ <b>{os.path.basename(script)} timed out.</b>",
            )
            return


def handle_analyze(bot_token, chat_id, groq_api_key, topic):
    if not topic.strip():
        send_message(
            bot_token, chat_id,
            "Send a topic, headline, or URL after /analyze. Example: <code>/analyze Dangote refinery campaign</code>",
        )
        return
    try:
        from groq import Groq
    except ImportError:
        send_message(bot_token, chat_id, "❌ Groq library not installed.")
        return

    send_message(bot_token, chat_id, "\U0001f9e0 Thinking...")
    try:
        client = Groq(api_key=groq_api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
                {"role": "user", "content": topic},
            ],
            temperature=0.5,
            max_tokens=1024,
        )
        analysis = response.choices[0].message.content.strip()
        header = f"<b>\U0001f4ca Strategic Analysis</b>\n<i>Topic: {topic[:200]}</i>\n{'=' * 30}\n\n"
        send_message(bot_token, chat_id, header + analysis)
    except Exception as e:
        send_message(bot_token, chat_id, f"❌ Groq error: <code>{str(e)[:300]}</code>")


def handle_sources(bot_token, chat_id):
    if not os.path.exists(SOURCES_FILE):
        send_message(
            bot_token, chat_id,
            "No sources.json yet — it's written on the first successful collector run.",
        )
        return
    try:
        with open(SOURCES_FILE, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        send_message(bot_token, chat_id, f"❌ Could not read sources.json: {e}")
        return

    queries = data.get("queries", [])
    feeds = data.get("feeds", [])
    last_queries = queries[-5:] if queries else []
    last_feeds = feeds[-3:] if feeds else []

    msg = (
        f"<b>\U0001f4cb Configured Sources</b>\n"
        f"Queries: <b>{len(queries)}</b>  |  Feeds: <b>{len(feeds)}</b>  |  "
        f"Total: <b>{len(queries) + len(feeds)}</b>\n\n"
        f"<b>Latest 5 queries:</b>\n"
    )
    for q in last_queries:
        msg += f"• {q}\n"
    msg += "\n<b>Latest 3 feeds:</b>\n"
    for feed in last_feeds:
        msg += f"• {feed}\n"
    send_message(bot_token, chat_id, msg)


def next_cron_run_wat():
    """Next scheduled digest in WAT based on the cron hours (UTC 2,5,8,11,14,17,20,23)."""
    cron_hours_utc = [2, 5, 8, 11, 14, 17, 20, 23]
    now_utc = datetime.now(timezone.utc)
    candidates = []
    for offset in (0, 1):
        base = (now_utc + timedelta(days=offset)).replace(minute=0, second=0, microsecond=0)
        for h in cron_hours_utc:
            candidate = base.replace(hour=h)
            if candidate > now_utc:
                candidates.append(candidate)
    if not candidates:
        return "unknown"
    nxt = min(candidates)
    return nxt.astimezone(WAT).strftime("%b %d, %I:%M %p WAT")


def handle_status(bot_token, chat_id):
    seen_count = 0
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                data = json.load(f)
            seen_count = len(data) if isinstance(data, (dict, list)) else 0
        except (json.JSONDecodeError, IOError):
            pass

    last_digest = "never"
    last_count = 0
    if os.path.exists(LAST_DIGEST_FILE):
        try:
            with open(LAST_DIGEST_FILE, "r") as f:
                d = json.load(f)
            last_digest = d.get("sent_at_wat", "never")
            last_count = d.get("article_count", 0)
        except (json.JSONDecodeError, IOError):
            pass

    total_sources = 0
    if os.path.exists(SOURCES_FILE):
        try:
            with open(SOURCES_FILE, "r") as f:
                s = json.load(f)
            total_sources = s.get("total", 0)
        except (json.JSONDecodeError, IOError):
            pass

    msg = (
        f"<b>\U0001f4c8 Ad Intel Status</b>\n\n"
        f"• Last digest: <b>{last_digest}</b> ({last_count} articles)\n"
        f"• Articles tracked (seen): <b>{seen_count}</b>\n"
        f"• Sources monitored: <b>{total_sources}</b>\n"
        f"• Next scheduled run: <b>{next_cron_run_wat()}</b>\n"
    )
    send_message(bot_token, chat_id, msg)


def process_message(bot_token, chat_id, groq_api_key, text):
    text = (text or "").strip()
    if not text:
        return

    lower = text.lower()
    if lower in ("/start", "/help"):
        handle_start(bot_token, chat_id)
    elif lower == "/digest":
        handle_digest(bot_token, chat_id)
    elif lower == "/sources":
        handle_sources(bot_token, chat_id)
    elif lower == "/status":
        handle_status(bot_token, chat_id)
    elif lower.startswith("/analyze"):
        topic = text[len("/analyze"):].strip()
        handle_analyze(bot_token, chat_id, groq_api_key, topic)
    elif text.startswith("/"):
        send_message(
            bot_token, chat_id,
            "Unknown command. Try /help for the full list.",
        )
    else:
        # plain text = /analyze shortcut
        handle_analyze(bot_token, chat_id, groq_api_key, text)


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    authorized_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    groq_api_key = os.environ.get("GROQ_API_KEY")

    if not bot_token:
        print("Error: TELEGRAM_BOT_TOKEN is not set.")
        sys.exit(1)
    if not authorized_chat_id:
        print("Error: TELEGRAM_CHAT_ID is not set.")
        sys.exit(1)

    try:
        authorized_chat_id_int = int(authorized_chat_id)
    except ValueError:
        print(f"Error: TELEGRAM_CHAT_ID must be an integer, got {authorized_chat_id!r}")
        sys.exit(1)

    offset = load_offset()
    print(f"Polling with offset={offset}...")
    updates = get_updates(bot_token, offset)
    print(f"Received {len(updates)} update(s).")

    processed = 0
    max_update_id = offset - 1
    for update in updates:
        update_id = update.get("update_id", 0)
        max_update_id = max(max_update_id, update_id)

        message = update.get("message")
        if not message:
            continue

        from_chat = message.get("chat", {}).get("id")
        if from_chat != authorized_chat_id_int:
            print(f"  Rejected message from unauthorized chat {from_chat}")
            continue

        text = message.get("text", "")
        print(f"  Processing: {text[:80]!r}")
        try:
            process_message(bot_token, str(authorized_chat_id_int), groq_api_key, text)
            processed += 1
        except Exception as e:
            print(f"  Handler error: {e}")
            try:
                send_message(
                    bot_token, str(authorized_chat_id_int),
                    f"❌ Handler error: <code>{str(e)[:300]}</code>",
                )
            except Exception:
                pass

    if max_update_id >= offset:
        save_offset(max_update_id + 1)

    print(f"Processed {processed} message(s). Next offset: {max_update_id + 1}")


if __name__ == "__main__":
    main()
