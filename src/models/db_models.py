from sqlalchemy import Column, Integer, String, Float, DateTime, Enum, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime
import enum
from src.database import Base

class InvoiceStatus(enum.Enum):
    PENDING = "pending"
    PAID = "paid"
    REVIEW = "review"

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    date = Column(DateTime, nullable=False)
    description = Column(String(500), nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="USD")
    vendor = Column(String(200), nullable=False, index=True)


class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(Integer, primary_key=True)
    vendor = Column(String(200), nullable=False)
    invoice_number = Column(String(50), unique=True, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String(3), default="USD")
    due_date = Column(DateTime, nullable=False)
    status = Column(Enum(InvoiceStatus), default=InvoiceStatus.PENDING)


class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"))
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True)

    confidence_score = Column(Float, nullable=False)
    agent_decision = Column(String(20))  # AUTO_MATCH, AUTO_REJECT, NEEDS_REVIEW, NO_MATCH
    agent_notes = Column(Text)

    human_decision = Column(String(20), nullable=True)  # APPROVED, REJECTED, MODIFIED
    human_notes = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    # relationships
    invoice = relationship("Invoice")
    transaction = relationship("Transaction")

    __table_args__ = (
        Index('ix_matches_invoice_id', 'invoice_id'),
        Index('ix_matches_human_decision', 'human_decision'),
    )