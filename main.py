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


async def _save_and_format(intent: str, text: str, extraction_path: str) -> str:
    """Extract, store, and return a confirmation line for one intent."""
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
        db.add_shopping(entry)
        items = ", ".join(entry.items)
        return f"✅  Shopping: {items} → {_fmt_date(entry.date)}"

    if intent == "learning":
        entry = ext.extract_learning(text)
        db.add_learning(entry)
        return f"📚  Learning: {entry.topic} → {_fmt_date(entry.date)}"

    # reminder / other
    entry = ext.extract_other(text)
    db.add_other(entry)
    return f"📌  Reminder: {entry.description} → {_fmt_date(entry.date)}"


async def _process(text: str, classifications: list[Classification]) -> list[str]:
    """Run the fast-gate decision and extract+store all detected intents."""
    path = decide(text, classifications)
    lines = []
    for cls in classifications:
        line = await _save_and_format(cls.intent, text, path)
        lines.append(line)
    return lines


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
    await update.message.reply_text(
        "Hi! I'm your personal AI life manager.\n\n"
        "Just type anything naturally:\n"
        "  • on weekend i will order face wash\n"
        "  • spent ₹200 on lunch today\n"
        "  • will learn system design tomorrow\n\n"
        "Commands:\n"
        "  /shopping  — your buy list\n"
        "  /learn     — your study list\n"
        "  /expenses  — this month's spending\n"
        "  /today     — due today\n"
        "  /done s5   — mark shopping item #5 done\n"
        "        l3   — learning #3 | o2 — reminder #2"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    _CHAT_IDS.add(update.effective_chat.id)

    # Store pending text in user_data (keyed by a short slot name).
    # Using user_data avoids stuffing text into callback_data (64-byte Telegram limit).
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

    # ── MEDIUM confidence: save + ask for confirmation ────────────────────────
    if top.confidence < CONFIDENCE_HIGH:
        lines = await _process(text, classifications)
        context.user_data["last_confirmation"] = "\n".join(lines)
        await update.message.reply_text(
            "\n".join(lines) + "\n\nWas that correct?",
            reply_markup=_confirm_keyboard(),
        )
        return

    # ── HIGH confidence: save silently ───────────────────────────────────────
    lines = await _process(text, classifications)
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
        lines = await _process(text, cls)
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


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: /done <prefix><id>\n"
            "Examples: /done s5  /done l3  /done o2\n"
            "Prefixes: s=shopping  l=learning  o=reminder"
        )
        return
    raw = context.args[0].lower().strip()
    prefix, num = raw[0], raw[1:]
    table = _PREFIX_MAP.get(prefix)
    if not table or not num.isdigit():
        await update.message.reply_text("Invalid format. Example: /done s5")
        return
    db.mark_done(table, int(num))
    await update.message.reply_text(f"Marked [{raw}] as done!")


# ── Scheduler job ─────────────────────────────────────────────────────────────

async def _daily_job(context: ContextTypes.DEFAULT_TYPE):
    for chat_id in list(_CHAT_IDS):
        try:
            await send_daily_notifications(context.bot, chat_id)
        except Exception as e:
            logger.error(f"Notification failed for {chat_id}: {e}")


# ── Boot ──────────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("shopping", cmd_shopping))
    app.add_handler(CommandHandler("learn",    cmd_learn))
    app.add_handler(CommandHandler("expenses", cmd_expenses))
    app.add_handler(CommandHandler("today",    cmd_today))
    app.add_handler(CommandHandler("done",     cmd_done))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Daily notification at 8:00 AM
    app.job_queue.run_daily(_daily_job, time=time(hour=8, minute=0))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
