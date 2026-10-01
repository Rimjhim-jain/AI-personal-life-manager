"""
Telegram Bot — entry point.

JEV-powered 3-tier confidence UX:
  >= 0.90  → silent save + confirmation message
  0.65-0.90 → save + inline "Was that right?" buttons
  < 0.65   → don't save, show 4 category buttons for user to pick

Compound intent support: one message can produce multiple DB entries
(e.g. "buy milk and spent ₹50 on tea" → shopping + expense rows).

Commands:
  /start    — welcome
  /shopping — pending shopping list
  /learn    — pending learning list
  /expenses [YYYY-MM] — expense summary
  /today    — items due today
  /done <prefix><id> — mark complete  (s5 = shopping #5, l3 = learning #3, o2 = others #2)
"""



import json
import logging
from datetime import date, time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import extractor as ext
import rules
from classifier import classify
from gate import decide
from query_handler import handle_query
from mailer import send_report
from scheduler import schedule_entry, restore_all
from config import CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, TELEGRAM_TOKEN
from models import Classification
from notifier import send_daily_notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

# Registered chat IDs so the scheduler knows where to send notifications.
# For a single-user app this is fine; extend to a DB column for multi-user.
_CHAT_IDS: set[int] = set()

# Table prefix → full table name
_PREFIX_MAP = {"s": "shopping", "l": "learning", "o": "others"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_date(d) -> str:
    if not d:
        return "no date set"
    try:
        return date.fromisoformat(str(d)).strftime("%a, %b %d")
    except Exception:
        return str(d)


def _schedule_if_timed(job_queue, chat_id: int, tbl: str, row_id: int, entry) -> str:
    """If entry has a reminder_time, schedule it and return a suffix string."""
    if not entry.reminder_time or not entry.date:
        return ""
    schedule_entry(
        job_queue, chat_id, tbl, row_id,
        date_str=str(entry.date),
        time_str=entry.reminder_time,
        description=getattr(entry, "topic", None)
                    or ", ".join(getattr(entry, "items", []))
                    or getattr(entry, "description", "Reminder"),
    )
    return f" · reminder at {entry.reminder_time}"


async def _save_and_format(intent: str, text: str, extraction_path: str,
                           job_queue=None, chat_id: int = 0) -> str:
    """Extract, store, schedule (if timed), and return a confirmation line."""
    if intent == "expense":
        if extraction_path == "rules":
            entry = rules.try_extract_expense(text, text)
            if not entry:
                entry = ext.extract_expense(text)
        else:
            entry = ext.extract_expense(text)
        db.add_expense(entry)
        return f"💸  Expense: ₹{entry.amount:.0f} · {entry.category} · {_fmt_date(entry.date)}"

    if intent == "shopping":
        entry = ext.extract_shopping(text)
        if entry.date is None:
            import datetime
            entry = entry.model_copy(update={"date": datetime.date.today()})
        row_id = db.add_shopping(entry)
        suffix = _schedule_if_timed(job_queue, chat_id, "shopping", row_id, entry) if job_queue else ""
        items = ", ".join(entry.items)
        return f"✅  Shopping: {items} → {_fmt_date(entry.date)}{suffix}"

    if intent == "learning":
        entry = ext.extract_learning(text)
        row_id = db.add_learning(entry)
        suffix = _schedule_if_timed(job_queue, chat_id, "learning", row_id, entry) if job_queue else ""
        return f"📚  Learning: {entry.topic} → {_fmt_date(entry.date)}{suffix}"

    # reminder / other
    entry = ext.extract_other(text)
    row_id = db.add_other(entry)
    suffix = _schedule_if_timed(job_queue, chat_id, "others", row_id, entry) if job_queue else ""
    return f"📌  Reminder: {entry.description} → {_fmt_date(entry.date)}{suffix}"


async def _process(text: str, classifications: list[Classification],
                   job_queue=None, chat_id: int = 0) -> list[str]:
    """Run the fast-gate decision and extract+store all detected intents.
    If the top intent is 'query', fetch from DB instead of storing.
    """
    # Query intent → retrieval, not storage
    if classifications and classifications[0].intent == "query":
        result = handle_query(text)
        return [result]

    path = decide(text, classifications)
    lines = []
    for cls in classifications:
        if cls.intent in ("query", "other"):
            continue
        line = await _save_and_format(cls.intent, text, path, job_queue, chat_id)
        lines.append(line)
    return lines or ["Saved!"]


def _clarify_keyboard(storage_key: str) -> InlineKeyboardMarkup:
    """4-button keyboard for low-confidence messages."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛒 Shopping",  callback_data=f"pick:shopping:{storage_key}"),
            InlineKeyboardButton("📚 Learning",  callback_data=f"pick:learning:{storage_key}"),
        ],
        [
            InlineKeyboardButton("💸 Expense",   callback_data=f"pick:expense:{storage_key}"),
            InlineKeyboardButton("📌 Reminder",  callback_data=f"pick:other:{storage_key}"),
        ],
    ])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Yes ✓",      callback_data="confirm:yes"),
        InlineKeyboardButton("No, redo ✗", callback_data="confirm:redo"),
    ]])


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _CHAT_IDS.add(update.effective_chat.id)
    db.save_chat_id(update.effective_chat.id)
    await update.message.reply_text(
        "Hi! I'm your personal AI life manager.\n\n"
        "Just type anything naturally:\n"
        "  • on weekend i will order face wash\n"
        "  • spent ₹200 on lunch today\n"
        "  • will learn system design tomorrow\n\n"
        "Commands:\n"
        "  /summary   — everything you've saved\n"
        "  /today     — what's due today\n"
        "  /shopping  — your buy list\n"
        "  /learn     — your study list\n"
        "  /expenses  — this month's spending\n"
        "  /mail      — email yourself a full report\n\n"
        "Completing tasks:\n"
        "  /manage    — tap-select multiple items ✓\n"
        "  /done s5   — mark one done\n"
        "  /done s1 s2 l3   — several at once\n"
        "  /done s1-s5      — a range\n"
        "  /done all        — everything pending\n"
        "  /clear     — permanently delete completed items"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    _CHAT_IDS.add(chat_id)
    db.save_chat_id(chat_id)  # persist for restart-survival

    context.user_data["pending"] = text

    thinking = await update.message.reply_text("...")

    classifications = classify(text)
    await thinking.delete()

    if not classifications:
        await update.message.reply_text("Hmm, couldn't understand that. Try again?")
        return

    top = classifications[0]

    # ── LOW confidence: ask user to pick ─────────────────────────────────────
    if top.confidence < CONFIDENCE_MEDIUM:
        await update.message.reply_text(
            f"Not sure what to do with this (confidence: {top.confidence:.0%}).\nPick a category:",
            reply_markup=_clarify_keyboard("pending"),
        )
        return

    jq = context.application.job_queue

    # ── MEDIUM confidence: save + ask for confirmation ────────────────────────
    if top.confidence < CONFIDENCE_HIGH:
        lines = await _process(text, classifications, jq, chat_id)
        context.user_data["last_confirmation"] = "\n".join(lines)
        await update.message.reply_text(
            "\n".join(lines) + "\n\nWas that correct?",
            reply_markup=_confirm_keyboard(),
        )
        return

    # ── HIGH confidence: save silently ───────────────────────────────────────
    lines = await _process(text, classifications, jq, chat_id)
    await update.message.reply_text("\n".join(lines))


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # User picked a category for a low-confidence message
    if data.startswith("pick:"):
        _, intent, _slot = data.split(":", 2)
        text = context.user_data.get("pending", "")
        if not text:
            await query.edit_message_text("Sorry, I lost that message. Please retype it.")
            return
        cls = [Classification(intent=intent, confidence=0.99)]
        chat_id = query.message.chat_id
        jq = context.application.job_queue
        lines = await _process(text, cls, jq, chat_id)
        await query.edit_message_text("\n".join(lines))

    # User confirmed medium-confidence save
    elif data == "confirm:yes":
        await query.edit_message_text(
            context.user_data.get("last_confirmation", "Saved!") + "\n\nSaved!"
        )

    # User rejected medium-confidence save — show category picker
    elif data == "confirm:redo":
        await query.edit_message_text(
            "No problem! Pick the correct category:",
            reply_markup=_clarify_keyboard("pending"),
        )

    # ── /manage interactions ─────────────────────────────────────────────────
    elif data.startswith("mg:"):
        action   = data[3:]
        items    = context.user_data.get("mg_items", [])
        selected = context.user_data.get("mg_selected", set())

        if action == "cancel":
            context.user_data.pop("mg_items", None)
            context.user_data.pop("mg_selected", None)
            await query.edit_message_text("Cancelled — nothing changed.")
            return

        if action == "apply":
            if not selected:
                await query.answer("Nothing selected yet", show_alert=True)
                return
            applied = []
            for key in sorted(selected):
                parsed = _parse_id(key)
                if parsed:
                    db.mark_done(*parsed)
                    applied.append(key)
            context.user_data.pop("mg_items", None)
            context.user_data.pop("mg_selected", None)
            await query.edit_message_text(
                f"Marked {len(applied)} item(s) done: {', '.join(applied)}"
            )
            return

        # Toggle one item's checkbox
        selected.symmetric_difference_update({action})
        context.user_data["mg_selected"] = selected
        await query.edit_message_reply_markup(
            reply_markup=_manage_keyboard(items, selected)
        )

    # ── /clear confirmation ──────────────────────────────────────────────────
    elif data == "clr:yes":
        removed = db.purge_done()
        await query.edit_message_text(f"Deleted {removed} completed item(s).")

    elif data == "clr:no":
        await query.edit_message_text("Cancelled — nothing deleted.")


async def cmd_shopping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_shopping_list()
    if not rows:
        await update.message.reply_text("Your shopping list is empty!")
        return
    lines = ["🛒  Shopping List\n"]
    for r in rows:
        items = json.loads(r["items"])
        lines.append(f"  [s{r['id']}]  {', '.join(items)}  —  {_fmt_date(r['date'])}")
    lines.append("\nUse /done s<id> to mark complete.")
    await update.message.reply_text("\n".join(lines))


async def cmd_learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_learning_list()
    if not rows:
        await update.message.reply_text("Your learning list is empty!")
        return
    lines = ["📚  Learning List\n"]
    for r in rows:
        res = f"  ({r['resource']})" if r.get("resource") else ""
        lines.append(f"  [l{r['id']}]  {r['topic']}{res}  —  {_fmt_date(r['date'])}")
    lines.append("\nUse /done l<id> to mark complete.")
    await update.message.reply_text("\n".join(lines))


async def cmd_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    month = args[0] if args else None
    rows = db.get_expenses(month)
    if not rows:
        label = month or date.today().strftime("%Y-%m")
        await update.message.reply_text(f"No expenses found for {label}.")
        return
    total = sum(r["amount"] for r in rows)
    label = month or date.today().strftime("%b %Y")
    lines = [f"💸  Expenses — {label}   Total: ₹{total:.0f}\n"]
    for r in rows:
        lines.append(
            f"  {r['date']}  ₹{r['amount']:.0f}  [{r['category']}]  {r['description'][:35]}"
        )
    await update.message.reply_text("\n".join(lines))


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_all_today()
    if not rows:
        await update.message.reply_text(
            f"Nothing due today ({date.today().strftime('%A, %b %d')})!"
        )
        return
    lines = [f"Today — {date.today().strftime('%A, %b %d')}\n"]
    for r in rows:
        tbl = r.get("tbl", "others")
        prefix = {"shopping": "s", "learning": "l", "others": "o"}.get(tbl, "o")
        if tbl == "shopping":
            items = json.loads(r.get("items", "[]"))
            lines.append(f"  [{prefix}{r['id']}]  🛒  {', '.join(items)}")
        elif tbl == "learning":
            lines.append(f"  [{prefix}{r['id']}]  📚  {r.get('topic', '')}")
        else:
            lines.append(f"  [{prefix}{r['id']}]  📌  {r.get('description', '')}")
    lines.append("\nUse /done <id> to mark complete.")
    await update.message.reply_text("\n".join(lines))


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Full dashboard — everything the user has saved, grouped by category."""
    sections = []

    # ── Stats header ─────────────────────────────────────────────────────────
    stats = db.get_summary_stats()
    month_label = date.today().strftime("%B %Y")
    sections.append(
        f"📊  Your Summary\n"
        f"  🛒 {stats['shopping']} shopping item(s) pending\n"
        f"  📚 {stats['learning']} learning item(s) pending\n"
        f"  📌 {stats['reminders']} reminder(s) pending\n"
        f"  💸 ₹{stats['expense_total']:.0f} spent this month ({stats['expense_count']} entries)"
    )

    # ── Shopping list ─────────────────────────────────────────────────────────
    s_rows = db.get_shopping_list()
    if s_rows:
        lines = ["🛒  Shopping List"]
        for r in s_rows:
            items = json.loads(r["items"])
            lines.append(f"  [s{r['id']}]  {', '.join(items)}  →  {_fmt_date(r['date'])}")
        sections.append("\n".join(lines))

    # ── Learning list ─────────────────────────────────────────────────────────
    l_rows = db.get_learning_list()
    if l_rows:
        lines = ["📚  Learning List"]
        for r in l_rows:
            res = f" ({r['resource']})" if r.get("resource") else ""
            lines.append(f"  [l{r['id']}]  {r['topic']}{res}  →  {_fmt_date(r['date'])}")
        sections.append("\n".join(lines))

    # ── Reminders ─────────────────────────────────────────────────────────────
    o_rows = db.get_others_list()
    if o_rows:
        lines = ["📌  Reminders"]
        for r in o_rows:
            lines.append(f"  [o{r['id']}]  {r['description']}  →  {_fmt_date(r['date'])}")
        sections.append("\n".join(lines))

    # ── Expenses this month ───────────────────────────────────────────────────
    e_rows = db.get_expenses()
    if e_rows:
        total = sum(r["amount"] for r in e_rows)
        lines = [f"💸  Expenses — {month_label}   Total: ₹{total:.0f}"]
        for r in e_rows[:10]:
            lines.append(f"  {r['date']}  ₹{r['amount']:.0f}  [{r['category']}]  {r['description'][:30]}")
        if len(e_rows) > 10:
            lines.append(f"  ... and {len(e_rows)-10} more. Use /expenses for full list.")
        sections.append("\n".join(lines))

    if len(sections) == 1:  # only stats header, nothing else
        sections.append("Nothing saved yet! Just type anything and I'll remember it.")

    sections.append("─────\nUse /done <id> to mark items complete.")
    await update.message.reply_text("\n\n".join(sections))


async def cmd_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a full HTML report to the configured email address."""
    await update.message.reply_text("Sending report to your email...")
    result = send_report()
    await update.message.reply_text(result)


def _parse_id(raw: str) -> tuple[str, int] | None:
    """'s5' → ('shopping', 5). Returns None if malformed."""
    raw = raw.lower().strip()
    if len(raw) < 2:
        return None
    table = _PREFIX_MAP.get(raw[0])
    num = raw[1:]
    if not table or not num.isdigit():
        return None
    return table, int(num)


def _expand_args(args: list[str]) -> list[str]:
    """Expand range syntax: 's1-s4' → ['s1','s2','s3','s4']. Passes others through."""
    out = []
    for a in args:
        a = a.lower().strip()
        if "-" in a:
            lo, hi = a.split("-", 1)
            p1 = _parse_id(lo)
            # allow both 's1-s4' and 's1-4'
            p2 = _parse_id(hi) if not hi.isdigit() else (p1[0] if p1 else None, int(hi))
            if p1 and p2 and p1[0] == p2[0] and p1[1] <= p2[1]:
                prefix = a[0]
                out.extend(f"{prefix}{n}" for n in range(p1[1], p2[1] + 1))
                continue
        out.append(a)
    return out


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: /done <id> [<id> ...]\n\n"
            "  /done s5              one item\n"
            "  /done s1 s2 l3 o2     several at once\n"
            "  /done s1-s5           a range\n"
            "  /done all             everything pending\n"
            "  /done all s           all shopping items\n\n"
            "Prefixes: s=shopping  l=learning  o=reminder\n"
            "Or use /manage to tap-select instead of typing."
        )
        return

    args = [a.lower().strip() for a in context.args]

    # ── "all" forms ──────────────────────────────────────────────────────────
    if args[0] == "all":
        only = _PREFIX_MAP.get(args[1][0]) if len(args) > 1 else None
        items = db.get_all_pending(limit=500)
        targets = [i for i in items if only is None or i["tbl"] == only]
        for it in targets:
            db.mark_done(it["tbl"], it["id"])
        label = only or "all categories"
        await update.message.reply_text(
            f"Marked {len(targets)} item(s) done in {label}." if targets
            else "Nothing pending to mark."
        )
        return

    # ── explicit IDs / ranges ────────────────────────────────────────────────
    ok, bad = [], []
    for raw in _expand_args(args):
        parsed = _parse_id(raw)
        if not parsed:
            bad.append(raw)
            continue
        try:
            db.mark_done(*parsed)
            ok.append(raw)
        except Exception:
            bad.append(raw)

    lines = []
    if ok:
        lines.append(f"Marked done: {', '.join(ok)}")
    if bad:
        lines.append(f"Couldn't parse: {', '.join(bad)}")
    await update.message.reply_text("\n".join(lines) or "Nothing to do.")


# ── /manage — tap-to-select ───────────────────────────────────────────────────

_ICON = {"shopping": "🛒", "learning": "📚", "others": "📌"}
_PFX  = {"shopping": "s", "learning": "l", "others": "o"}


def _item_label(it: dict) -> str:
    label = it["label"] or ""
    if it["tbl"] == "shopping":
        try:
            label = ", ".join(json.loads(label))
        except Exception:
            pass
    return label[:28]


def _manage_keyboard(items: list[dict], selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for it in items:
        key = f"{_PFX[it['tbl']]}{it['id']}"
        box = "☑" if key in selected else "☐"
        rows.append([InlineKeyboardButton(
            f"{box}  {_ICON[it['tbl']]} {_item_label(it)}",
            callback_data=f"mg:{key}",
        )])
    rows.append([
        InlineKeyboardButton(f"✓ Mark Done ({len(selected)})", callback_data="mg:apply"),
        InlineKeyboardButton("✗ Cancel",                       callback_data="mg:cancel"),
    ])
    return InlineKeyboardMarkup(rows)


async def cmd_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = db.get_all_pending()
    if not items:
        await update.message.reply_text("Nothing pending — you're all clear!")
        return
    context.user_data["mg_items"]    = items
    context.user_data["mg_selected"] = set()
    await update.message.reply_text(
        "Tap to select, then press Mark Done:",
        reply_markup=_manage_keyboard(items, set()),
    )


# ── /clear — purge completed rows ─────────────────────────────────────────────

async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = db.count_done()
    if n == 0:
        await update.message.reply_text("No completed items to clear.")
        return
    await update.message.reply_text(
        f"Permanently delete {n} completed item(s) from the database?\n"
        "This cannot be undone.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Yes, delete", callback_data="clr:yes"),
            InlineKeyboardButton("Cancel",      callback_data="clr:no"),
        ]]),
    )


# ── Scheduler job ─────────────────────────────────────────────────────────────

async def _daily_job(context: ContextTypes.DEFAULT_TYPE):
    for chat_id in list(_CHAT_IDS):
        try:
            await send_daily_notifications(context.bot, chat_id)
        except Exception as e:
            logger.error(f"Notification failed for {chat_id}: {e}")


# ── Boot ──────────────────────────────────────────────────────────────────────

async def _post_init(app):
    """Called once after the bot connects — restore all timed reminders from DB."""
    restore_all(app.job_queue)


def main():
    db.init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(_post_init).build()

    # Register handlers
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("summary",  cmd_summary))
    app.add_handler(CommandHandler("mail",     cmd_mail))
    app.add_handler(CommandHandler("shopping", cmd_shopping))
    app.add_handler(CommandHandler("learn",    cmd_learn))
    app.add_handler(CommandHandler("expenses", cmd_expenses))
    app.add_handler(CommandHandler("today",    cmd_today))
    app.add_handler(CommandHandler("done",     cmd_done))
    app.add_handler(CommandHandler("manage",   cmd_manage))
    app.add_handler(CommandHandler("clear",    cmd_clear))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Daily notification at 8:00 AM
    app.job_queue.run_daily(_daily_job, time=time(hour=8, minute=0))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
