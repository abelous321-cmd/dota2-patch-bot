#!/usr/bin/env python3
"""
Dota 2 Patch Notes -> Discord Webhook Notifier
------------------------------------------------
Checks Valve's official Steam News feed for Dota 2 (AppID 570) for new
patch/update posts, and sends any new ones to a Discord webhook.

Designed to be run on a schedule (cron, GitHub Actions, systemd timer, etc.)
It keeps track of which posts it has already sent in a small local JSON file
(state.json) so it won't post duplicates.

SETUP
-----
1. Install dependencies:
     pip install requests

2. Get a Discord webhook URL:
     Discord server -> Server Settings -> Integrations -> Webhooks -> New Webhook
     Copy the Webhook URL.

3. Set it as an environment variable (recommended) or edit DISCORD_WEBHOOK_URL below:
     export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxxx/yyyy"

4. Run it manually to test:
     python3 dota2_patch_notifier.py

5. Schedule it to run periodically, e.g. every 30 minutes via cron:
     */30 * * * * cd /path/to/script && /usr/bin/python3 dota2_patch_notifier.py >> notifier.log 2>&1

   Or use a GitHub Actions scheduled workflow (see README notes at bottom).
"""

import json
import os
import re
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DOTA2_APP_ID = 570
STEAM_NEWS_URL = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v0002/"
STATE_FILE = Path(__file__).parent / "state.json"

# You can hardcode your webhook URL here instead of using an env var,
# but env var is safer (keeps secrets out of source control).
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# How many recent news items to fetch/check each run
NEWS_COUNT = 15

# Only treat posts matching these patterns as "patches" (case-insensitive).
# Dota 2 patch posts are typically titled like "Gameplay Update 7.38" or
# "7.38 Gameplay Update", "Patch 7.38c", etc.
PATCH_TITLE_PATTERNS = [
    r"gameplay update",
    r"\bpatch\b",
    r"\b7\.\d{2}[a-z]?\b",   # version numbers like 7.38, 7.38b
]


# ---------------------------------------------------------------------------
# State handling (avoid re-posting the same patch)
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
        "maxlength": 600,
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
    """Strip BBCode/HTML-ish tags Steam sometimes includes, and trim length."""
    text = re.sub(r"\[.*?\]", "", contents)  # strip [bbcode] tags
    text = re.sub(r"<.*?>", "", text)         # strip html tags
    text = text.strip()
    if len(text) > 800:
        text = text[:800].rsplit(" ", 1)[0] + "..."
    return text


# ---------------------------------------------------------------------------
# Sending to Discord
# ---------------------------------------------------------------------------

def send_to_discord(webhook_url: str, item: dict) -> None:
    embed = {
        "title": item["title"][:256],
        "url": item["url"],
        "description": clean_contents(item.get("contents", "")),
        "color": 0xE03C31,  # Dota red
        "footer": {"text": "Dota 2 Patch Notes • via Steam News"},
    }

    payload = {
        "username": "Dota 2 Patch Bot",
        "content": f"📢 New Dota 2 update: **{item['title']}**",
        "embeds": [embed],
    }

    resp = requests.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not DISCORD_WEBHOOK_URL:
        print("ERROR: DISCORD_WEBHOOK_URL is not set. Set it as an env var "
              "or edit the script.", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    sent_ids = set(state.get("sent_ids", []))

    news_items = fetch_dota2_news()
    patch_items = [n for n in news_items if is_patch_post(n["title"])]

    # Oldest first, so they post to Discord in chronological order
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
