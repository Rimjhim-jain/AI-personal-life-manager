from pydantic import BaseModel
from datetime import date
from typing import Optional, List


class Classification(BaseModel):
    intent: str  # shopping | learning | expense | reminder | other
    confidence: float


class ShoppingEntry(BaseModel):
    items: List[str]
    date: Optional[date] = None
    platform: Optional[str] = None
    notes: Optional[str] = None
    raw_text: str


class LearningEntry(BaseModel):
    topic: str
    date: Optional[date] = None
    resource: Optional[str] = None
    notes: Optional[str] = None
    raw_text: str


class ExpenseEntry(BaseModel):
    amount: float
    currency: str = "INR"
    category: Optional[str] = "other"
    description: str
    date: date
    raw_text: str


class OtherEntry(BaseModel):
    description: str
    date: Optional[date] = None
    notes: Optional[str] = None
    raw_text: str
