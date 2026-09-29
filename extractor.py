"""
Layer 3 — Groq Entity Extractor

Calls Groq (LLaMA 3.3-70b, free tier) to pull structured data out of
the user's raw text for each detected intent.

Handles relative dates ("tomorrow", "this weekend", "next Monday") via
the dateparser library.
"""

import json
import logging
from datetime import date
from typing import Optional

import dateparser
from groq import Groq

from models import ShoppingEntry, LearningEntry, ExpenseEntry, OtherEntry
from config import GROQ_API_KEY

logger = logging.getLogger(__name__)
_client = Groq(api_key=GROQ_API_KEY)

_BASE = "Today is {today}. Resolve relative dates (tomorrow, weekend, next Monday, etc.) to YYYY-MM-DD."

_PROMPTS = {
    "shopping": _BASE + """
Extract shopping details. Return JSON:
{"items": ["item1", "item2"], "date": "YYYY-MM-DD or null", "platform": "amazon/flipkart/etc or null", "notes": "extra info or null"}""",

    "learning": _BASE + """
Extract study/learning details. Return JSON:
{"topic": "what to learn", "date": "YYYY-MM-DD or null", "resource": "book/video/course name or null", "notes": "extra info or null"}""",

    "expense": _BASE + """
Extract expense details. Return JSON:
{"amount": 123.45, "currency": "INR", "category": "food|transport|grocery|shopping|utilities|health|entertainment|other", "description": "brief description", "date": "YYYY-MM-DD"}""",

    "other": _BASE + """
Extract reminder/task details. Return JSON:
{"description": "what to remember or do", "date": "YYYY-MM-DD or null", "notes": "extra info or null"}""",

    "reminder": _BASE + """
Extract reminder details. Return JSON:
{"description": "what to remember or do", "date": "YYYY-MM-DD or null", "notes": "extra info or null"}""",
}


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value or value in ("null", "None", ""):
        return None
    try:
        parsed = dateparser.parse(value, settings={"PREFER_DATES_FROM": "future"})
        return parsed.date() if parsed else None
    except Exception:
        return None


def _call(prompt: str, text: str) -> dict:
    resp = _client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": prompt.format(today=date.today().isoformat())},
            {"role": "user",   "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=300,
    )
    return json.loads(resp.choices[0].message.content)


def extract_shopping(text: str) -> ShoppingEntry:
    data = _call(_PROMPTS["shopping"], text)
    return ShoppingEntry(
        items=data.get("items") or [text],
        date=_parse_date(data.get("date")),
        platform=data.get("platform"),
        notes=data.get("notes"),
        raw_text=text,
    )


def extract_learning(text: str) -> LearningEntry:
    data = _call(_PROMPTS["learning"], text)
    return LearningEntry(
        topic=data.get("topic") or text,
        date=_parse_date(data.get("date")),
        resource=data.get("resource"),
        notes=data.get("notes"),
        raw_text=text,
    )


def extract_expense(text: str) -> ExpenseEntry:
    data = _call(_PROMPTS["expense"], text)
    return ExpenseEntry(
        amount=float(data.get("amount") or 0),
        currency=data.get("currency") or "INR",
        category=data.get("category") or "other",
        description=data.get("description") or text,
        date=_parse_date(data.get("date")) or date.today(),
        raw_text=text,
    )


def extract_other(text: str) -> OtherEntry:
    intent_key = "other"
    data = _call(_PROMPTS[intent_key], text)
    return OtherEntry(
        description=data.get("description") or text,
        date=_parse_date(data.get("date")),
        notes=data.get("notes"),
        raw_text=text,
    )


def extract_for_intent(intent: str, text: str):
    """Dispatch to the right extractor based on intent string."""
    dispatch = {
        "shopping": extract_shopping,
        "learning": extract_learning,
        "expense":  extract_expense,
        "reminder": extract_other,
        "other":    extract_other,
    }
    fn = dispatch.get(intent, extract_other)
    return fn(text)
