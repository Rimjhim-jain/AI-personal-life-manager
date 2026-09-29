"""
Layer 2 — Fast Gate

Decides whether to use the fast rule-based extractor (rules.py) or the
slower Groq LLM extractor (extractor.py) for a given message.

JEV runs in ~200ms; this gate preserves Groq free-tier quota by skipping
the LLM call when the message is a simple, unambiguous expense with a
clear amount pattern.
"""

import logging
from typing import List
from models import Classification
from rules import is_simple_expense
from config import CONFIDENCE_HIGH

logger = logging.getLogger(__name__)


def decide(text: str, classifications: List[Classification]) -> str:
    """
    Returns 'rules' or 'groq'.

    'rules' path: fast regex extraction, no API call, <5ms.
    'groq' path:  full LLM entity extraction, handles complex/ambiguous text.

    Only uses 'rules' when:
      - The top intent is 'expense'
      - Confidence >= CONFIDENCE_HIGH (0.90)
      - The text matches the simple amount+verb pattern
    """
    top = max(classifications, key=lambda c: c.confidence, default=None)

    if (
        top is not None
        and top.intent == "expense"
        and top.confidence >= CONFIDENCE_HIGH
        and is_simple_expense(text)
    ):
        logger.debug(f"Gate → rules  (expense, conf={top.confidence:.2f}) '{text[:40]}'")
        return "rules"

    logger.debug(f"Gate → groq   (intent={top.intent if top else '?'}) '{text[:40]}'")
    return "groq"
