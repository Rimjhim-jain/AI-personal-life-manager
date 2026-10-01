"""
Handles natural language retrieval queries.

Examples:
  "give me next week plans"
  "what did I spend this month"
  "show my shopping list"
  "total expenses in September"

Flow: Groq parses { category, period } → fetch from DB → format reply.
"""

import json
import logging
from datetime import date, timedelta
from groq import Groq

import database as db
from config import GROQ_API_KEY

logger = logging.getLogger(__name__)
_client = Groq(api_key=GROQ_API_KEY)

_PROMPT = """Parse the user's query about their personal life-manager data.
Return JSON with exactly these keys:
{{"category": "expenses|shopping|learning|plans|all", "period": "today|this_week|next_week|this_month|all"}}

Rules:
- plans = learning + shopping + reminders together
- 'all' period = no time filter
- If they say 'this month' or 'monthly', use this_month
- Today is {today}"""


def _parse_query(text: str) -> dict:
    resp = _client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": _PROMPT.format(today=date.today().isoformat())},
            {"role": "user",   "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=80,
    )
    return json.loads(resp.choices[0].message.content)


def _week_range(offset: int = 0):
    """Return (start_str, end_str) for this week (offset=0) or next (offset=1)."""
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    sunday = monday + timedelta(days=6)
    return str(monday), str(sunday)


def _in_range(date_str, start, end) -> bool:
    if not date_str:
        return False
    return start <= date_str <= end


def handle_query(text: str) -> str:
    try:
        parsed = _parse_query(text)
    except Exception as e:
        logger.error(f"Query parse failed: {e}")
        return (
            "Couldn't understand your query.\n"
            "Try: /shopping  /learn  /expenses  /today"
        )

    category = parsed.get("category", "all")
    period   = parsed.get("period", "all")

    # Resolve period to concrete values
    today_str    = str(date.today())
    month_str    = date.today().strftime("%Y-%m")
    period_label = {
        "today":      "Today",
        "this_week":  "This week",
        "next_week":  "Next week",
        "this_month": date.today().strftime("%B %Y"),
        "all":        "All time",
    }.get(period, period)

    if period in ("this_week", "next_week"):
        week_start, week_end = _week_range(0 if period == "this_week" else 1)

    sections = []

    # ── Expenses ─────────────────────────────────────────────────────────────
    if category in ("expenses", "all"):
        if period in ("this_month", "all"):
            rows = db.get_expenses(month_str if period == "this_month" else None)
        else:
            rows = db.get_expenses(None)  # fetch all, filter below
            if period == "today":
                rows = [r for r in rows if r["date"] == today_str]
            elif period in ("this_week", "next_week"):
                rows = [r for r in rows if _in_range(r["date"], week_start, week_end)]

        if rows:
            total = sum(r["amount"] for r in rows)
            lines = [f"💸 Expenses — {period_label}   Total: ₹{total:.0f}\n"]
            for r in rows[:15]:
                lines.append(f"  {r['date']}  ₹{r['amount']:.0f}  [{r['category']}]  {r['description'][:35]}")
            sections.append("\n".join(lines))
        elif category == "expenses":
            sections.append(f"No expenses found for {period_label}.")

    # ── Shopping ─────────────────────────────────────────────────────────────
    if category in ("shopping", "plans", "all"):
        rows = db.get_shopping_list()
        if period == "today":
            rows = [r for r in rows if r.get("date") == today_str]
        elif period in ("this_week", "next_week"):
            rows = [r for r in rows if _in_range(r.get("date"), week_start, week_end)]

        if rows:
            lines = [f"🛒 Shopping — {period_label}\n"]
            for r in rows[:10]:
                items = json.loads(r["items"])
                lines.append(f"  [s{r['id']}]  {', '.join(items)}  →  {r['date'] or 'no date'}")
            sections.append("\n".join(lines))

    # ── Learning ─────────────────────────────────────────────────────────────
    if category in ("learning", "plans", "all"):
        rows = db.get_learning_list()
        if period == "today":
            rows = [r for r in rows if r.get("date") == today_str]
        elif period in ("this_week", "next_week"):
            rows = [r for r in rows if _in_range(r.get("date"), week_start, week_end)]

        if rows:
            lines = [f"📚 Learning — {period_label}\n"]
            for r in rows[:10]:
                res = f"  ({r['resource']})" if r.get("resource") else ""
                lines.append(f"  [l{r['id']}]  {r['topic']}{res}  →  {r['date'] or 'no date'}")
            sections.append("\n".join(lines))

    if not sections:
        return f"Nothing found for: {period_label}."

    return "\n\n".join(sections)
