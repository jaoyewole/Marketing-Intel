#!/usr/bin/env python3
"""Process articles using Groq LLM for categorization and relevance scoring."""

import json
import os
import re
import sys
import time

from groq import Groq


DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
NEW_FILE = os.path.join(DATA_DIR, "new_articles.json")
PROCESSED_FILE = os.path.join(DATA_DIR, "processed_articles.json")
BATCH_SIZE = 10
MAX_RETRIES = 3
BACKOFF_BASE = 2

SYSTEM_PROMPT = """You are a Nigerian advertising and marketing intelligence analyst.
For each article, provide:
1. CATEGORY: One of [Agency News, Brand Campaign, Industry Regulation, People Moves, Digital/Tech, Media Spend, Industry Trend, Event/Award, International]
2. RELEVANCE: Score 1-10 (10 = directly about Nigerian ad/marketing industry, 1 = barely related)
3. SUMMARY: One crisp sentence summarising the key takeaway for a marketing professional.
4. SENTIMENT: One of [Positive, Negative, Neutral]

Only include articles with relevance >= 5.

Return ONLY a valid JSON array of objects with keys: title, url, source, category, relevance, summary, sentiment. No markdown, no preamble, no explanation."""


def load_articles():
    """Load new articles from disk."""
    if not os.path.exists(NEW_FILE):
        return []
    try:
        with open(NEW_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, IOError):
        return []


def parse_json_response(text):
    """Parse JSON from Groq response, with fallback regex extraction."""
    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try to extract JSON array from response
    match = re.search(r'\[[\s\S]*\]', text)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    print(f"  Warning: Could not parse JSON from response")
    return []


def process_batch(client, articles):
    """Send a batch of articles to Groq for processing."""
    user_content = json.dumps([
        {"title": a["title"], "url": a["url"], "source": a["source"], "snippet": a.get("snippet", "")}
        for a in articles
    ], indent=2)

    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            text = response.choices[0].message.content
            return parse_json_response(text)
        except Exception as e:
            wait = BACKOFF_BASE ** (attempt + 1)
            print(f"  Groq API error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
    return []


def main():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("Error: GROQ_API_KEY environment variable is not set.")
        sys.exit(1)

    articles = load_articles()
    if not articles:
        print("No new articles to process.")
        sys.exit(0)

    print(f"Processing {len(articles)} articles with Groq...")
    client = Groq(api_key=api_key)

    all_processed = []
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE
        print(f"  Batch {batch_num}/{total_batches} ({len(batch)} articles)...")
        results = process_batch(client, batch)
        # Filter to relevance >= 5
        relevant = [r for r in results if r.get("relevance", 0) >= 5]
        all_processed.extend(relevant)
        # Small delay between batches to respect rate limits
        if i + BATCH_SIZE < len(articles):
            time.sleep(1)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PROCESSED_FILE, "w") as f:
        json.dump(all_processed, f, indent=2)

    print(f"Processed: {len(all_processed)} relevant articles (relevance >= 5) out of {len(articles)} total.")


if __name__ == "__main__":
    main()
