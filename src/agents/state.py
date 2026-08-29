"""
State schema for the LangGraph reconciliation agent.

This module defines the structure of the state object that flows through
the agent's nodes. The state is a TypedDict that allows partial updates
(total=False) so that each node can add or modify fields as needed.

Fields:
    - invoice_id: ID of the invoice being reconciled.
    - transaction_id: ID of the chosen matched transaction (if any).
    - invoice_data: Full invoice record fetched from the database.
    - transaction_data: Full transaction record for the chosen match.
    - candidates: List of candidate transactions with similarity scores.
    - analysis: LLM's reasoning summary.
    - confidence: Confidence score (0.0–1.0).
    - decision: Agent's decision category.
    - notes: Human-readable notes for the match record.
"""

from typing import Any, Dict, List, Literal, Optional

from typing_extensions import TypedDict

# Valid decision values (use Literal for type safety)
AgentDecisionLiteral = Literal["AUTO_MATCH", "NEEDS_REVIEW", "NO_MATCH"]


class ReconciliationState(TypedDict, total=False):
    """
    State object passed between nodes in the reconciliation graph.

    Attributes:
        invoice_id: ID of the invoice being reconciled.
        transaction_id: ID of the transaction selected as the best match (None if no match).
        invoice_data: Dictionary containing full invoice fields (vendor, amount, due_date, etc.).
        transaction_data: Dictionary containing full transaction fields for the match.
        candidates: List of candidate transactions, each with id, description, amount, date, similarity.
        analysis: Free-text reasoning from the LLM evaluation.
        confidence: Float between 0.0 and 1.0 indicating match confidence.
        decision: One of "AUTO_MATCH", "NEEDS_REVIEW", or "NO_MATCH".
        notes: Concatenated notes for the match record (includes analysis and decision context).
    """
    invoice_id: int
    transaction_id: Optional[int]
    invoice_data: Dict[str, Any]
    transaction_data: Dict[str, Any]
    candidates: List[Dict[str, Any]]  # each: {id, description, amount, date, similarity}
    analysis: Optional[str]
    confidence: float
    decision: AgentDecisionLiteral
    notes: str


def create_initial_state(invoice_id: int) -> ReconciliationState:
    """
    Create a default initial state for a given invoice.

    This helper ensures that all required fields are present with sensible defaults,
    making it easier to start the agent workflow.

    Args:
        invoice_id: ID of the invoice to reconcile.

    Returns:
        ReconciliationState: A state dict with defaults set.
    """
    return {
        "invoice_id": invoice_id,
        "transaction_id": None,
        "invoice_data": {},
        "transaction_data": {},
        "candidates": [],
        "analysis": None,
        "confidence": 0.0,
        "decision": "NO_MATCH",
        "notes": "",
    }