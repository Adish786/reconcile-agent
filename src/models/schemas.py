from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class MatchBase(BaseModel):
    invoice_id: int
    transaction_id: Optional[int] = None
    confidence_score: float
    agent_decision: str
    agent_notes: str

class MatchCreate(MatchBase):
    pass

class MatchOut(MatchBase):
    id: int
    human_decision: Optional[str]
    human_notes: Optional[str]
    reviewed_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True

class ReviewUpdateRequest(BaseModel):
    decision: str  # APPROVED or REJECTED
    notes: Optional[str] = None

class ReconcileResponse(BaseModel):
    invoice_id: int
    match_id: int
    decision: str
    confidence: float