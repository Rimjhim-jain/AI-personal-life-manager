"""
Timed reminder scheduler — restart-survival version.

How it survives restarts:
  1. reminder_time is persisted in the DB alongside the entry
  2. On every bot startup, restore_all() re-reads pending entries and re-queues them
  3. chat_id is also persisted in the settings table, so the bot knows
     where to send even before the user sends their first post-restart message

Flow for a new entry:
  user: "remind me at 6 PM to order pizza"
  → saved to DB with date=today, reminder_time="18:00"
  → schedule_entry() queues a run_once job at 18:00 today
  → at 18:00 → Telegram message sent → notified=1 in DB

Flow on restart:
  → restore_all() reads all rows where reminder_time IS NOT NULL AND notified=0 AND date >= today
  → re-queues each one; if datetime already passed, fires in 10 seconds
"""

import json
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import database as db

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Kolkata")


async def _fire_reminder(context):
    """APScheduler job callback — sends Telegram message and marks notified."""
    data    = context.job.data
    chat_id = context.job.chat_id

    desc = data.get("description", "Reminder!")
    # Shopping entries store items as JSON string
    if data.get("tbl") == "shopping":
        try:
            items = json.loads(desc)
            desc = "Buy: " + ", ".join(items)
        except Exception:
            pass

    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Reminder: {desc}"
    )
    db.mark_notified(data["tbl"], data["row_id"])
    logger.info(f"Fired reminder {data['tbl']}#{data['row_id']}")


def _build_fire_dt(date_str: str, time_str: str) -> datetime | None:
    """Combine date + HH:MM strings into a timezone-aware datetime (IST)."""
    try:
        d = date.fromisoformat(date_str)
        h, m = map(int, time_str.split(":"))
        return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)
    except Exception as e:
        logger.error(f"Cannot build fire datetime from '{date_str}' '{time_str}': {e}")
        return None


def schedule_entry(job_queue, chat_id: int, tbl: str, row_id: int,
                   date_str: str, time_str: str, description: str):
    """
    Queue a one-shot reminder job.
    Idempotent: removes any existing job with the same name before re-adding.
    """
    fire_at = _build_fire_dt(date_str, time_str)
    if not fire_at:
        return

    now = datetime.now(TZ)
    if fire_at <= now:
        # Missed window (past time) — fire almost immediately
        fire_at = now + timedelta(seconds=10)
        logger.info(f"Reminder {tbl}#{row_id} is past due — firing in 10 s")

    job_name = f"reminder_{tbl}_{row_id}"

    # Remove duplicate if already queued (e.g. re-save after restart)
    for existing in job_queue.get_jobs_by_name(job_name):
        existing.schedule_removal()

    job_queue.run_once(
        _fire_reminder,
        when=fire_at,
        data={"tbl": tbl, "row_id": row_id, "description": description},
        chat_id=chat_id,
        name=job_name,
    )
    logger.info(f"Scheduled '{job_name}' at {fire_at.strftime('%Y-%m-%d %H:%M %Z')}")


def restore_all(job_queue):
    """
    Re-queue all pending timed reminders from DB after a bot restart.
    Called from post_init hook in main.py.
    """
    chat_id = db.get_chat_id()
    if not chat_id:
        logger.info("No stored chat_id yet — skipping reminder restore (will restore after first message)")
        return

    pending = db.get_pending_timed_reminders()
    count = 0
    for row in pending:
        if not row.get("reminder_time") or not row.get("date"):
            continue
        schedule_entry(
            job_queue,
            chat_id=chat_id,
            tbl=row["tbl"],
            row_id=row["id"],
            date_str=row["date"],
            time_str=row["reminder_time"],
            description=row.get("description", "Reminder"),
        )
        count += 1

    if count:
        logger.info(f"Restored {count} timed reminder(s) from DB")
    else:
        logger.info("No pending timed reminders to restore")
