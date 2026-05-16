"""
PartRecon Telegram bot — posts new listings to a Telegram channel.
Runs every 15 minutes via cron. Checks for listings added in the last 15 minutes.

Setup:
  1. Create a bot via @BotFather on Telegram — set TELEGRAM_BOT_TOKEN in .env
  2. Create a channel and add the bot as admin — set TELEGRAM_CHANNEL_ID in .env
  3. Run once to test: python scripts/telegram_bot.py
"""
import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from database.db import get_connection

# Load .env manually (no python-dotenv dependency)
_env = Path(__file__).parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

BOT_TOKEN  = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
LOOKBACK   = 15  # minutes


def get_recent_listings():
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LOOKBACK)).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, title, source, price, url, listing_type, post_type
               FROM listings
               WHERE created_at >= ?
               ORDER BY created_at DESC""",
            (cutoff,),
        ).fetchall()
    return rows


def format_message(row):
    listing_id, title, source, price, url, ltype, post_type = row
    lines = [f"<b>{title}</b>"]
    if price:
        lines.append(f"💰 ${price:,.0f}")
    tags = []
    if post_type and post_type != "UNKNOWN":
        tags.append(post_type)
    if ltype == "vehicle":
        tags.append("Vehicle")
    if tags:
        lines.append(" · ".join(tags))
    lines.append(f"📍 {source}")
    lines.append(f'<a href="https://partrecon.com/listing/{listing_id}">View on PartRecon</a>')
    return "\n".join(lines)


async def post_listings(listings):
    from telegram import Bot
    bot = Bot(token=BOT_TOKEN)
    for row in listings:
        text = format_message(row)
        try:
            await bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            print(f"  Failed to send: {e}")


def main():
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        print("TELEGRAM_BOT_TOKEN not set in .env — skipping.")
        return
    if not CHANNEL_ID or CHANNEL_ID == "your_channel_id_here":
        print("TELEGRAM_CHANNEL_ID not set in .env — skipping.")
        return

    listings = get_recent_listings()
    print(f"Found {len(listings)} new listing(s) in last {LOOKBACK} minutes.")
    if listings:
        asyncio.run(post_listings(listings))
        print("Done.")


if __name__ == "__main__":
    main()
