"""
SQLAlchemy ORM models for the Reconcile Agent.

This module defines the database tables:
- transactions: Bank or payment transactions.
- invoices: Customer invoices awaiting reconciliation.
- matches: Reconciliation matches between invoices and transactions.
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship, Mapped, mapped_column

from src.database import Base


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------
class InvoiceStatus(enum.Enum):
    """Possible statuses of an invoice."""
    PENDING = "PENDING"
    PAID = "PAID"
    REVIEW = "REVIEW"


class AgentDecision(str, enum.Enum):
    """Possible decisions made by the reconciliation agent."""
    AUTO_MATCH = "AUTO_MATCH"
    AUTO_REJECT = "AUTO_REJECT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NO_MATCH = "NO_MATCH"


class HumanDecision(str, enum.Enum):
    """Possible decisions made by a human reviewer."""
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class Transaction(Base):
    """
    Bank or payment transaction that may match an invoice.

    Attributes:
        id: Primary key.
        date: Transaction date.
        description: Transaction description (e.g., payment reference).
        amount: Monetary value.
        currency: Currency code (ISO 4217).
        vendor: Vendor name – used for matching with invoices.
    """
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False, index=True)
    description = Column(String(500), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="USD")
    vendor = Column(String(200), nullable=False, index=True)

    # Relationship back to matches
    matches = relationship("Match", back_populates="transaction")

    def __repr__(self) -> str:
        return f"<Transaction(id={self.id}, vendor='{self.vendor}', amount={self.amount})>"


class Invoice(Base):
    """
    Customer invoice to be reconciled.

    Attributes:
        id: Primary key.
        vendor: Vendor or customer name.
        invoice_number: Unique invoice identifier.
        amount: Total invoice amount.
        currency: Currency code.
        due_date: Payment due date.
        status: Current status (pending, paid, review).
    """
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    vendor = Column(String(200), nullable=False, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="USD")
    due_date = Column(DateTime, nullable=False, index=True)
    status = Column(Enum(InvoiceStatus), default=InvoiceStatus.PENDING, index=True)

    # Relationship back to matches
    matches = relationship("Match", back_populates="invoice")

    def __repr__(self) -> str:
        return f"<Invoice(id={self.id}, number='{self.invoice_number}', vendor='{self.vendor}')>"


class Match(Base):
    """
    Reconciliation match between an invoice and a transaction.

    This table stores the agent's decision, confidence, and any human review.

    Attributes:
        id: Primary key.
        invoice_id: Foreign key to invoices.
        transaction_id: Foreign key to transactions (nullable if no match).
        confidence_score: Agent's confidence (0.0–1.0).
        agent_decision: Decision category (AUTO_MATCH, NEEDS_REVIEW, etc.).
        agent_notes: Reasoning or notes from the agent.
        human_decision: Human reviewer's decision (if reviewed).
        human_notes: Human reviewer's notes.
        reviewed_at: Timestamp of human review.
        created_at: Timestamp of match creation.
    """
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True, index=True)

    confidence_score = Column(Float, nullable=False)
    agent_decision = Column(String(20), nullable=False, index=True)
    agent_notes = Column(Text, nullable=True)  # can be long

    human_decision = Column(String(20), nullable=True, index=True)
    human_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships for ORM navigation
    invoice = relationship("Invoice", back_populates="matches")
    transaction = relationship("Transaction", back_populates="matches")

    # Composite indexes for common query patterns
    __table_args__ = (
        Index("ix_matches_invoice_transaction", "invoice_id", "transaction_id"),
        Index("ix_matches_agent_decision_human_decision", "agent_decision", "human_decision"),
    )

    def __repr__(self) -> str:
        return (
            f"<Match(id={self.id}, invoice_id={self.invoice_id}, "
            f"transaction_id={self.transaction_id}, decision='{self.agent_decision}')>"
        )