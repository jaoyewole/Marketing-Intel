#!/usr/bin/env python3
"""Fetch articles from Google News RSS and Nigerian trade press feeds."""

import json
import os
import sys
import time
import calendar
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

import feedparser
import requests


GOOGLE_NEWS_QUERIES = [
    "Nigerian advertising",
    "Nigeria marketing agency",
    "APCON Nigeria",
    "Nigeria brand campaign",
    "Nigeria media agency",
    "Lagos advertising",
    "Nigeria digital marketing",
    "OAAN Nigeria",
    "AAAN Nigeria",
    "Nigeria PR agency",
    "Nigeria creative agency",
    "Nigeria ad spend",
    "campaign",
    "brand launch",
]

TRADE_PRESS_FEEDS = [
    "https://marketingedge.com.ng/feed/",
    "https://brandcom.ng/feed/",
    "https://www.businessdayng.com/category/marketing/feed/",
    "https://guardian.ng/category/business-services/marketing/feed/",
    "https://brandcampaign.com.ng/feed/",
]

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SEEN_FILE = os.path.join(DATA_DIR, "seen_articles.json")
NEW_FILE = os.path.join(DATA_DIR, "new_articles.json")
SOURCES_FILE = os.path.join(DATA_DIR, "sources.json")
MAX_SEEN = 2000
FEED_TIMEOUT = 15
FRESHNESS_WINDOW = timedelta(hours=3, minutes=30)


def load_seen():
    """Load seen article URLs from disk."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return {url: True for url in data}
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_seen(seen):
    """Save seen article URLs, capping at MAX_SEEN most recent."""
    items = list(seen.items())
    if len(items) > MAX_SEEN:
        items = items[-MAX_SEEN:]
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(dict(items), f, indent=2)


def save_sources():
    """Emit current queries + feeds so the Telegram /sources command can read them."""
    os.makedirs(DATA_DIR, exist_ok=True)
    data = {
        "queries": GOOGLE_NEWS_QUERIES,
        "feeds": TRADE_PRESS_FEEDS,
        "total_queries": len(GOOGLE_NEWS_QUERIES),
        "total_feeds": len(TRADE_PRESS_FEEDS),
        "total": len(GOOGLE_NEWS_QUERIES) + len(TRADE_PRESS_FEEDS),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(SOURCES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_feed(url):
    """Fetch and parse a single RSS feed with timeout."""
    try:
        resp = requests.get(url, timeout=FEED_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AdIntelBot/1.0)"
        })
        resp.raise_for_status()
        return feedparser.parse(resp.content)
    except Exception as e:
        print(f"  Warning: Failed to fetch {url}: {e}")
        return None


def is_fresh(entry, cutoff_utc):
    """Return True only if the entry has a published date within the freshness window."""
    published_parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not published_parsed:
        return False
    try:
        published_utc = datetime.fromtimestamp(calendar.timegm(published_parsed), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return False
    return published_utc >= cutoff_utc


def extract_articles(feed, source_name, cutoff_utc, stats):
    """Extract fresh article data from a parsed feed."""
    articles = []
    if not feed or not hasattr(feed, "entries"):
        return articles
    for entry in feed.entries:
        url = getattr(entry, "link", "")
        if not url:
            continue
        if not is_fresh(entry, cutoff_utc):
            stats["stale"] += 1
            continue
        stats["fresh"] += 1
        title = getattr(entry, "title", "No title")
        published = getattr(entry, "published", "")
        snippet = getattr(entry, "summary", "")
        if snippet and len(snippet) > 500:
            snippet = snippet[:500] + "..."
        articles.append({
            "title": title,
            "url": url,
            "source": source_name,
            "published": published,
            "snippet": snippet,
        })
    return articles


def main():
    save_sources()

    now_utc = datetime.now(timezone.utc)
    cutoff_utc = now_utc - FRESHNESS_WINDOW
    print(f"Freshness cutoff (UTC): {cutoff_utc.isoformat()} (last 3h30m)")

    seen = load_seen()
    all_new = []
    feeds_checked = 0
    stats = {"fresh": 0, "stale": 0}

    # Google News RSS feeds — when=3h forces recency server-side
    for query in GOOGLE_NEWS_QUERIES:
        encoded = quote(query)
        url = (
            f"https://news.google.com/rss/search?q={encoded}"
            f"&hl=en-NG&gl=NG&ceid=NG:en&when=3h"
        )
        print(f"Fetching Google News: {query}")
        feed = fetch_feed(url)
        feeds_checked += 1
        articles = extract_articles(feed, f"Google News ({query})", cutoff_utc, stats)
        for art in articles:
            if art["url"] not in seen:
                all_new.append(art)
                seen[art["url"]] = True

    # Direct trade press RSS feeds — client-side freshness filter
    for url in TRADE_PRESS_FEEDS:
        print(f"Fetching trade press: {url}")
        feed = fetch_feed(url)
        feeds_checked += 1
        source = url.split("//")[1].split("/")[0] if "//" in url else url
        articles = extract_articles(feed, source, cutoff_utc, stats)
        for art in articles:
            if art["url"] not in seen:
                all_new.append(art)
                seen[art["url"]] = True

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(NEW_FILE, "w") as f:
        json.dump(all_new, f, indent=2)

    save_seen(seen)

    print(
        f"\nFiltered: {stats['fresh']} articles within freshness window, "
        f"{stats['stale']} articles rejected as stale."
    )
    print(f"Found {len(all_new)} new articles from {feeds_checked} feeds.")


if __name__ == "__main__":
    main()
