"""
Pydantic schemas for the Reconcile Agent API.

These models define the structure of request and response payloads,
and include validation logic to ensure data integrity.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ----------------------------------------------------------------------
# Enums for decision types
# ----------------------------------------------------------------------
class AgentDecision(str, Enum):
    """Possible decisions made by the agent."""
    AUTO_MATCH = "AUTO_MATCH"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_MATCH = "NO_MATCH"


class HumanDecision(str, Enum):
    """Possible decisions made by a human reviewer."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class InvoiceStatus(str, Enum):
    """Possible statuses of an invoice."""
    PENDING = "PENDING"
    PAID = "PAID"
    REVIEW = "REVIEW"


# ----------------------------------------------------------------------
# Base schemas
# ----------------------------------------------------------------------
class MatchBase(BaseModel):
    """
    Base schema for a match record.

    Attributes:
        invoice_id: Foreign key to the invoice.
        transaction_id: Foreign key to the transaction (optional if no match).
        confidence_score: Score between 0.0 and 1.0 indicating match certainty.
        agent_decision: Decision made by the reconciliation agent.
        agent_notes: Notes or reasoning from the agent.
    """
    invoice_id: int = Field(..., description="ID of the invoice being reconciled")
    transaction_id: Optional[int] = Field(None, description="ID of the matched transaction, if any")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    agent_decision: AgentDecision = Field(..., description="Decision made by the agent")
    agent_notes: str = Field(..., min_length=1, description="Agent's reasoning or notes")

    @field_validator("agent_notes")
    @classmethod
    def validate_notes(cls, v: str) -> str:
        """Ensure notes are not empty strings."""
        if not v or not v.strip():
            raise ValueError("agent_notes must not be empty")
        return v.strip()


class MatchCreate(MatchBase):
    """Schema for creating a new match (used internally)."""
    pass


# ----------------------------------------------------------------------
# Output schemas
# ----------------------------------------------------------------------
class MatchOut(MatchBase):
    """
    Schema for returning a match record from the API.

    Includes additional fields that are generated after creation.
    """
    id: int = Field(..., description="Unique match ID")
    human_decision: Optional[HumanDecision] = Field(None, description="Human's decision (if reviewed)")
    human_notes: Optional[str] = Field(None, description="Human's review notes")
    reviewed_at: Optional[datetime] = Field(None, description="Timestamp when reviewed")
    created_at: datetime = Field(..., description="Timestamp when match was created")

    class Config:
        from_attributes = True  # Enables ORM mode for SQLAlchemy models


# ----------------------------------------------------------------------
# Request/Response schemas for specific endpoints
# ----------------------------------------------------------------------
class ReviewUpdateRequest(BaseModel):
    """
    Schema for updating a match with a human decision.

    Attributes:
        decision: Must be "APPROVED" or "REJECTED".
        notes: Optional notes from the reviewer.
    """
    decision: HumanDecision = Field(..., description="Human decision: APPROVED or REJECTED")
    notes: Optional[str] = Field(None, max_length=500, description="Optional review notes (max 500 chars)")


class ReconcileResponse(BaseModel):
    """
    Response schema for the reconciliation endpoint.

    Attributes:
        invoice_id: The ID of the reconciled invoice.
        match_id: The ID of the created match (0 if no match).
        decision: The final decision (e.g., APPROVED, NO_MATCH, etc.).
        confidence: The confidence score of the decision.
    """
    invoice_id: int = Field(..., description="Invoice ID")
    match_id: int = Field(..., description="Match ID (0 if no match)")
    decision: str = Field(..., description="Final decision (e.g., APPROVED, NO_MATCH)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score")