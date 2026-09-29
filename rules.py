"""
Fast rule-based extractor for simple, high-confidence expense messages.
Used by gate.py to skip the Groq API call when the pattern is unambiguous.

Example: "spent 200 on lunch today" → ExpenseEntry without any LLM call.
"""

import re
from datetime import date, timedelta
from typing import Optional
from models import ExpenseEntry

# Matches amounts like: ₹200, rs 200, 200rs, 200 rupees, INR 200
_AMOUNT_RE = re.compile(
    r'(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)'   # prefix style: ₹200, rs 200
    r'|'
    r'([\d,]+(?:\.\d+)?)\s*(?:rs\.?|inr|₹|rupees?)',  # suffix style: 200 rs
    re.IGNORECASE,
)

_EXPENSE_VERB_RE = re.compile(
    r'\b(spent|paid|bought|cost|charged|bill|expense)\b', re.IGNORECASE
)

_TODAY_RE    = re.compile(r'\btoday\b', re.IGNORECASE)
_YESTERDAY_RE = re.compile(r'\byesterday\b', re.IGNORECASE)

_CATEGORY_KEYWORDS = {
    "food":          ["lunch", "dinner", "breakfast", "meal", "food", "restaurant",
                      "snack", "coffee", "tea", "cafe", "pizza", "burger", "biryani", "chai"],
    "transport":     ["auto", "cab", "uber", "ola", "bus", "metro", "rickshaw",
                      "petrol", "fuel", "train", "taxi", "rapido"],
    "grocery":       ["grocery", "groceries", "vegetables", "fruits", "milk",
                      "sabzi", "kirana", "doodh"],
    "shopping":      ["clothes", "shirt", "shoes", "dress", "amazon", "flipkart", "online"],
    "utilities":     ["electricity", "internet", "wifi", "phone", "recharge", "bill", "broadband"],
    "health":        ["medicine", "doctor", "pharmacy", "medical", "hospital", "clinic"],
    "entertainment": ["movie", "netflix", "prime", "spotify", "game", "concert"],
}


def _guess_category(text: str) -> str:
    lower = text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return category
    return "other"


def _resolve_date(text: str) -> date:
    if _TODAY_RE.search(text):
        return date.today()
    if _YESTERDAY_RE.search(text):
        return date.today() - timedelta(days=1)
    return date.today()


def is_simple_expense(text: str) -> bool:
    """True when the text has both an amount pattern and an expense verb/symbol."""
    has_amount  = bool(_AMOUNT_RE.search(text))
    has_trigger = bool(_EXPENSE_VERB_RE.search(text)) or bool(
        re.search(r'₹|rs\.?', text, re.IGNORECASE)
    )
    return has_amount and has_trigger


def try_extract_expense(text: str, raw_text: str) -> Optional[ExpenseEntry]:
    """
    Extract an ExpenseEntry using regex only — no LLM call.
    Returns None if no amount found.
    """
    match = _AMOUNT_RE.search(text)
    if not match:
        return None

    amount_str = (match.group(1) or match.group(2)).replace(",", "")
    try:
        amount = float(amount_str)
    except ValueError:
        return None

    return ExpenseEntry(
        amount=amount,
        currency="INR",
        category=_guess_category(text),
        description=text.strip(),
        date=_resolve_date(text),
        raw_text=raw_text,
    )
