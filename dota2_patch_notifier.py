#!/usr/bin/env python3
"""
Dota 2 Patch Notes -> Discord Webhook Notifier (Russian translation)
----------------------------------------------------------------------
Checks Valve's official Steam News feed for Dota 2 (AppID 570) for new
patch/update posts, translates them to Russian, and sends the full text
to a Discord webhook.

Designed to be run on a schedule (cron, GitHub Actions, systemd timer, etc.)
It keeps track of which posts it has already sent in a small local JSON file
(state.json) so it won't post duplicates.

SETUP
-----
1. Install dependencies:
     pip install requests deep-translator

2. Set your webhook URL as an environment variable:
     export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxxx/yyyy"

3. Run it manually to test:
     python3 dota2_patch_notifier.py
"""

import json
import os
import re
import sys
from pathlib import Path

import requests
from deep_translator import GoogleTranslator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOTA2_APP_ID = 570
STEAM_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/"
STATE_FILE = Path(__file__).parent / "state.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# How many recent news items to fetch/check each run
NEWS_COUNT = 15

# Discord embed description hard limit is 4096 characters; stay under it.
DISCORD_DESCRIPTION_LIMIT = 4000

# Google Translate (via deep-translator) has a ~5000 char limit per call,
# so we chunk long patch notes before translating.
TRANSLATE_CHUNK_SIZE = 4000

PATCH_TITLE_PATTERNS = [
    r"gameplay update",
    r"\bpatch\b",
    r"\b7\.\d{2}[a-z]?\b",
]


# ---------------------------------------------------------------------------
# State handling
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"sent_ids": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Fetching news from Steam
# ---------------------------------------------------------------------------

def fetch_dota2_news() -> list:
    params = {
        "appid": DOTA2_APP_ID,
        "count": NEWS_COUNT,
        "maxlength": 0,  # 0 = no truncation, return full contents
        "format": "json",
    }
    resp = requests.get(STEAM_NEWS_URL, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("appnews", {}).get("newsitems", [])


def is_patch_post(title: str) -> bool:
    title_lower = title.lower()
    return any(re.search(pattern, title_lower) for pattern in PATCH_TITLE_PATTERNS)


def clean_contents(contents: str) -> str:
    """Strip BBCode/HTML-ish tags Steam includes, keep full text."""
    text = re.sub(r"\[.*?\]", "", contents)  # strip [bbcode] tags
    text = re.sub(r"<.*?>", "", text)         # strip html tags
    text = re.sub(r"\n{3,}", "\n\n", text)    # collapse excess blank lines
    return text.strip()


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

def translate_to_russian(text: str) -> str:
    if not text:
        return text
    translator = GoogleTranslator(source="auto", target="ru")

    if len(text) <= TRANSLATE_CHUNK_SIZE:
        return translator.translate(text)

    # Chunk on paragraph breaks to keep translation coherent
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > TRANSLATE_CHUNK_SIZE:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)

    translated_chunks = [translator.translate(chunk) for chunk in chunks]
    return "\n".join(translated_chunks)


# ---------------------------------------------------------------------------
# Sending to Discord
# ---------------------------------------------------------------------------

def build_embeds(title_ru: str, url: str, body_ru: str) -> list:
    """Split long translated text across multiple embeds if needed,
    since a single embed description is capped at ~4096 chars."""
    if len(body_ru) <= DISCORD_DESCRIPTION_LIMIT:
        return [{
            "title": title_ru[:256],
            "url": url,
            "description": body_ru,
            "color": 0xE03C31,
            "footer": {"text": "Dota 2 Patch Notes • via Steam News (переведено)"},
        }]

    # Split into multiple embeds (Discord allows up to 10 per message)
    parts = []
    remaining = body_ru
    while remaining:
        parts.append(remaining[:DISCORD_DESCRIPTION_LIMIT])
        remaining = remaining[DISCORD_DESCRIPTION_LIMIT:]

    embeds = []
    for i, part in enumerate(parts[:10]):
        embeds.append({
            "title": title_ru[:256] if i == 0 else None,
            "url": url if i == 0 else None,
            "description": part,
            "color": 0xE03C31,
            "footer": {"text": "Dota 2 Patch Notes • via Steam News (переведено)"} if i == len(parts) - 1 else None,
        })
    return embeds


def send_to_discord(webhook_url: str, item: dict) -> None:
    title_ru = translate_to_russian(item["title"])
    body_ru = translate_to_russian(clean_contents(item.get("contents", "")))

    embeds = build_embeds(title_ru, item["url"], body_ru)

    payload = {
        "username": "Dota 2 Patch Bot",
        "content": f"📢 Новое обновление Dota 2: **{title_ru}**",
        "embeds": embeds,
    }

    resp = requests.post(webhook_url, json=payload, timeout=30)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not DISCORD_WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL is not set.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    sent_ids = set(state.get("sent_ids", []))

    news_items = fetch_dota2_news()
    patch_items = [n for n in news_items if is_patch_post(n["title"])]
    patch_items.sort(key=lambda n: n["date"])

    new_count = 0
    for item in patch_items:
        item_id = item["gid"]
        if item_id in sent_ids:
            continue

        print(f"New patch found: {item['title']}")
        send_to_discord(DISCORD_WEBHOOK_URL, item)
        sent_ids.add(item_id)
        new_count += 1

    state["sent_ids"] = list(sent_ids)
    save_state(state)

    if new_count == 0:
        print("No new patches.")
    else:
        print(f"Sent {new_count} new patch notice(s) to Discord.")


if __name__ == "__main__":
    main()
