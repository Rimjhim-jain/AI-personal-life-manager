"""
Layer 1 — Intent Classification

Primary:  JEV (typesafe.ai) — typed, calibrated, multi-label, ~200ms
Fallback: Groq LLM (llama-3.3-70b) with JSON mode — same structured output

JEV is used when JEV_API_KEY is set and the call succeeds.
The fallback kicks in automatically on any JEV failure.
"""

import json
import logging
import requests
from typing import List
from models import Classification
from config import GROQ_API_KEY, JEV_API_KEY, CONFIDENCE_MEDIUM

logger = logging.getLogger(__name__)

INTENTS = ["shopping", "learning", "expense", "reminder", "other"]

_GROQ_SYSTEM_PROMPT = """You are an intent classifier for a personal life manager app.

Classify the user's message into one or more of these intents:
- shopping   : user wants to buy, order, or get something
- learning   : user wants to study, learn, read, or understand something
- expense    : user spent, paid, or recorded money
- reminder   : user wants to be reminded about something (appointments, tasks)
- other      : anything else

Return ONLY valid JSON in this exact format:
{"classifications": [{"intent": "shopping", "confidence": 0.95}, {"intent": "expense", "confidence": 0.88}]}

Rules:
- A single message CAN have multiple intents — include ALL that apply
- confidence is a float between 0.0 and 1.0
- Only include intents with confidence > 0.5
- Be precise with confidence: 0.95+ = very clear signal, 0.7 = likely, 0.55 = possible"""


def _classify_with_jev(text: str) -> List[Classification]:
    """
    Call TypeSafe AI JEV API.
    JEV returns typed, calibrated multi-label classifications in ~200ms.
    API shape is inferred from TypeSafe AI's published architecture.
    """
    resp = requests.post(
        "https://api.typesafe.ai/v1/classify",
        headers={
            "Authorization": f"Bearer {JEV_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"text": text, "labels": INTENTS},
        timeout=5,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        Classification(intent=c["label"], confidence=c["confidence"])
        for c in data.get("classifications", [])
        if c["confidence"] > CONFIDENCE_MEDIUM
    ]


def _classify_with_groq(text: str) -> List[Classification]:
    """
    Groq LLM fallback using JSON mode.
    Mimics JEV's structured multi-label output.
    """
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": _GROQ_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
        max_tokens=200,
    )
    data = json.loads(resp.choices[0].message.content)
    return [
        Classification(intent=c["intent"], confidence=c["confidence"])
        for c in data.get("classifications", [])
        if c["confidence"] > CONFIDENCE_MEDIUM
    ]


def classify(text: str) -> List[Classification]:
    """
    Classify text into one or more intents with calibrated confidence scores.

    Returns list ordered by confidence descending.
    Each item: Classification(intent, confidence)

    Confidence tiers (see config.py):
      >= 0.90  → save silently
      0.65-0.90 → save + ask confirmation
      < 0.65   → ask user to pick category
    """
    if JEV_API_KEY:
        try:
            results = _classify_with_jev(text)
            if results:
                logger.debug(f"JEV classified '{text[:40]}': {results}")
                return sorted(results, key=lambda c: c.confidence, reverse=True)
        except Exception as e:
            logger.warning(f"JEV failed ({e}), falling back to Groq")

    try:
        results = _classify_with_groq(text)
        logger.debug(f"Groq classified '{text[:40]}': {results}")
        return sorted(results, key=lambda c: c.confidence, reverse=True)
    except Exception as e:
        logger.error(f"Groq classification also failed: {e}")
        return [Classification(intent="other", confidence=0.5)]
