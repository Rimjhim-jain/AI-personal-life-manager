import datetime
from pydantic import BaseModel
from typing import Optional, List


class Classification(BaseModel):
    intent: str  # shopping | learning | expense | reminder | other
    confidence: float


class ShoppingEntry(BaseModel):
    items: List[str]
    date: Optional[datetime.date] = None
    reminder_time: Optional[str] = None  # "HH:MM" 24h, e.g. "18:00"
    platform: Optional[str] = None
    notes: Optional[str] = None
    raw_text: str


class LearningEntry(BaseModel):
    topic: str
    date: Optional[datetime.date] = None
    reminder_time: Optional[str] = None
    resource: Optional[str] = None
    notes: Optional[str] = None
    raw_text: str


class ExpenseEntry(BaseModel):
    amount: float
    currency: str = "INR"
    category: Optional[str] = "other"
    description: str
    date: datetime.date
    raw_text: str


class OtherEntry(BaseModel):
    description: str
    date: Optional[datetime.date] = None
    reminder_time: Optional[str] = None
    notes: Optional[str] = None
    raw_text: str
