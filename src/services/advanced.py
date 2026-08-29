"""
Advanced reconciliation service – LLM‑based matching using a LangGraph agent.

This module builds a LangGraph workflow that:
1. Retrieves the invoice and candidate transactions.
2. Uses an LLM to reason about the best match.
3. Routes to a decision (AUTO_MATCH, NEEDS_REVIEW, NO_MATCH).
4. Persists the match record in the database.
"""

import logging
from typing import Dict, Union

from sqlalchemy.orm import Session

from src.agents.graph import build_reconciliation_graph
from src.models.db_models import Invoice, Match
from src.models.schemas import AgentDecision

# Constants
DEFAULT_CONFIDENCE = 0.0
DEFAULT_DECISION = "NO_MATCH"

logger = logging.getLogger(__name__)


def run_advanced(invoice_id: int, db: Session) -> Dict[str, Union[str, int, float]]:
    """
    Perform LLM-based reconciliation for a given invoice using a LangGraph agent.

    The agent retrieves candidate transactions, invokes an LLM to evaluate them,
    and produces a decision with confidence. The result is persisted as a Match record.

    Args:
        invoice_id: ID of the invoice to reconcile.
        db: SQLAlchemy session.

    Returns:
        dict: Contains status, decision, confidence, and match_id (or error info).
    """
    # 1. Verify invoice exists FIRST (avoids unnecessary graph building)
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        logger.warning(f"Invoice {invoice_id} not found")
        return {
            "status": "ERROR",
            "reason": "Invoice not found",
            "decision": DEFAULT_DECISION,
            "confidence": DEFAULT_CONFIDENCE,
            "match_id": 0,
        }

    # 2. Build the graph dynamically (allows mocking in tests)
    app_graph = build_reconciliation_graph()

    logger.info(f"Running advanced reconciliation for invoice {invoice_id}")

    # 3. Prepare initial state for the graph
    initial_state = {
        "invoice_id": invoice_id,
        "transaction_id": None,
        "invoice_data": {},
        "transaction_data": {},
        "candidates": [],
        "analysis": "",
        "confidence": DEFAULT_CONFIDENCE,
        "decision": DEFAULT_DECISION,
        "notes": "",
    }

    # 4. Invoke the LangGraph workflow
    try:
        final_state = app_graph.invoke(initial_state)
        logger.debug(f"Final state from graph: {final_state}")
    except Exception as e:
        logger.error(f"LangGraph invocation failed for invoice {invoice_id}: {str(e)}", exc_info=True)
        return {
            "status": "ERROR",
            "reason": f"Agent execution error: {str(e)}",
            "decision": DEFAULT_DECISION,
            "confidence": DEFAULT_CONFIDENCE,
            "match_id": 0,
        }

    # 5. Extract and validate decision fields
    decision = final_state.get("decision", DEFAULT_DECISION)
    confidence = float(final_state.get("confidence", DEFAULT_CONFIDENCE))
    transaction_id = final_state.get("transaction_id")
    notes = final_state.get("notes", "")

    if decision not in [d.value for d in AgentDecision]:
        logger.warning(f"Unexpected decision '{decision}', falling back to NO_MATCH")
        decision = AgentDecision.NO_MATCH

    # 6. Create and persist Match record
    try:
        match = Match(
            invoice_id=invoice_id,
            transaction_id=transaction_id,
            confidence_score=confidence,
            agent_decision=decision,
            agent_notes=notes,
        )
        db.add(match)
        db.commit()
        db.refresh(match)
        logger.info(f"Match created with id={match.id}, decision={decision}, confidence={confidence}")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to persist match: {e}", exc_info=True)
        return {
            "status": "ERROR",
            "reason": f"Database error: {str(e)}",
            "decision": DEFAULT_DECISION,
            "confidence": DEFAULT_CONFIDENCE,
            "match_id": 0,
        }

    # 7. Return result
    return {
        "status": decision,
        "decision": decision,
        "confidence": confidence,
        "match_id": match.id,
        "transaction_id": transaction_id,
    }