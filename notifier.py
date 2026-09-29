"""
Daily notification sender.
Called by APScheduler at 8 AM every day.
Sends a Telegram message for every item due today, then marks them notified.
"""

import json
import logging
from database import get_due_today, mark_notified

logger = logging.getLogger(__name__)


async def send_daily_notifications(bot, chat_id: int):
    due = get_due_today()
    if not due:
        return

    lines = [f"Good morning! Here's what's on for today:\n"]
    for row in due:
        tbl = row.get("tbl", "others")
        if tbl == "shopping":
            items = json.loads(row.get("items", "[]"))
            lines.append(f"🛒  Buy: {', '.join(items)}")
        elif tbl == "learning":
            lines.append(f"📚  Learn: {row.get('topic', '')}")
        else:
            lines.append(f"📌  {row.get('description', '')}")
        mark_notified(tbl, row["id"])

    await bot.send_message(chat_id=chat_id, text="\n".join(lines))
    logger.info(f"Sent {len(due)} notifications to {chat_id}")
