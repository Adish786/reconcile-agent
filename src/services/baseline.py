"""
Baseline reconciliation service – rule-based matching.

This module provides a simple deterministic matching logic:
- Matches an invoice with a transaction if vendor matches exactly
  and amount is within a small tolerance (±0.01).
- Creates a Match record with decision AUTO_MATCH or NO_MATCH.
"""

import logging
from datetime import datetime
from typing import Dict, Union

from sqlalchemy.orm import Session

from src.models.db_models import Invoice, Match, Transaction
from src.models.schemas import AgentDecision, HumanDecision

# Configuration constants
AMOUNT_TOLERANCE: float = 0.01
CONFIDENCE_MATCH: float = 0.95
CONFIDENCE_NO_MATCH: float = 0.0

logger = logging.getLogger(__name__)


def run_baseline(invoice_id: int, db: Session) -> Dict[str, Union[str, int, float]]:
    """
    Perform rule-based reconciliation for a given invoice.

    Tries to find a transaction with the same vendor and an amount within ±0.01.
    If found, creates a Match with AUTO_MATCH and confidence 0.95.
    Otherwise, creates a Match with NO_MATCH and confidence 0.0.

    Args:
        invoice_id: ID of the invoice to reconcile.
        db: SQLAlchemy session.

    Returns:
        dict: Contains status, decision, confidence, and match_id.

    Raises:
        None – all errors are caught and returned as status "ERROR".
    """
    try:
        # 1. Fetch the invoice
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            logger.warning(f"Invoice {invoice_id} not found")
            return {
                "status": "ERROR",
                "reason": "Invoice not found",
                "decision": "NO_MATCH",
                "confidence": 0.0,
                "match_id": 0,
            }

        logger.info(f"Running baseline for invoice {invoice_id} (vendor='{invoice.vendor}', amount={invoice.amount})")

        # 2. Find a matching transaction
        candidate = (
            db.query(Transaction)
            .filter(
                Transaction.vendor == invoice.vendor,
                Transaction.amount.between(
                    invoice.amount - AMOUNT_TOLERANCE,
                    invoice.amount + AMOUNT_TOLERANCE,
                ),
            )
            .first()
        )

        # 3. Create a Match record
        if candidate:
            match = Match(
                invoice_id=invoice.id,
                transaction_id=candidate.id,
                confidence_score=CONFIDENCE_MATCH,
                agent_decision=AgentDecision.AUTO_MATCH,
                agent_notes=(
                    f"Baseline match: vendor '{invoice.vendor}', "
                    f"amount {invoice.amount} matched transaction {candidate.id}"
                ),
                created_at=datetime.utcnow(),
            )
            decision_out = "APPROVED"
            confidence_out = CONFIDENCE_MATCH
            status_out = "AUTO_MATCH"
            logger.info(f"Match found: transaction {candidate.id}")
        else:
            match = Match(
                invoice_id=invoice.id,
                transaction_id=None,
                confidence_score=CONFIDENCE_NO_MATCH,
                agent_decision=AgentDecision.NO_MATCH,
                agent_notes=(
                    f"No transaction found for vendor '{invoice.vendor}', "
                    f"amount {invoice.amount} within tolerance ±{AMOUNT_TOLERANCE}"
                ),
                created_at=datetime.utcnow(),
            )
            decision_out = "NO_MATCH"
            confidence_out = CONFIDENCE_NO_MATCH
            status_out = "NO_MATCH"
            logger.info("No matching transaction found")

        # 4. Commit the match record
        db.add(match)
        db.commit()
        db.refresh(match)

        return {
            "status": status_out,
            "decision": decision_out,
            "confidence": confidence_out,
            "match_id": match.id,
        }

    except Exception as e:
        db.rollback()
        logger.error(f"Baseline reconciliation failed for invoice {invoice_id}: {str(e)}", exc_info=True)
        return {
            "status": "ERROR",
            "reason": f"Database error: {str(e)}",
            "decision": "NO_MATCH",
            "confidence": 0.0,
            "match_id": 0,
        }