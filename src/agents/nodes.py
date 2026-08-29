"""
LangGraph nodes for the reconciliation agent.

This module defines the three nodes of the agent workflow:
1. retrieve_node: Fetches invoice data and candidate transactions.
2. evaluate_node: Uses an LLM to reason about candidates and decide on a match.
3. route_node: Finalizes the decision and prepares the output notes.

Each node takes a ReconciliationState and returns a dict of updates to the state.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from src.agents.state import ReconciliationState
from src.agents.tools import calculate_date_diff, db_fetch_candidates, db_fetch_invoice
from src.config import settings
from src.models.schemas import AgentDecision
from src.utils.fuzzy import amount_within_tolerance

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
DEFAULT_CONFIDENCE = 0.0
DEFAULT_DECISION = "NO_MATCH"
DEFAULT_ANALYSIS = ""
CANDIDATE_LIMIT = 5  # Number of top candidates to send to LLM
SIMILARITY_THRESHOLD = 80  # Percentage threshold for fuzzy match
DATE_TOLERANCE_DAYS = 5  # Days allowed between invoice due date and transaction date
HEURISTIC_CONFIDENCE_HIGH = 0.95
HEURISTIC_CONFIDENCE_MEDIUM = 0.75
HEURISTIC_CONFIDENCE_LOW = 0.1

# ----------------------------------------------------------------------
# LLM client (initialized once at module load)
# ----------------------------------------------------------------------
llm = ChatOpenAI(
    model="gemini-1.5-flash",
    temperature=0.1,
    openai_api_key=settings.OPENAI_API_KEY,
    openai_api_base=settings.OPENAI_BASE_URL,
)


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def _parse_llm_response(response_content: str, candidates: List[Dict]) -> Dict[str, Any]:
    """
    Parse the LLM's JSON response with fallback to heuristic matching.

    Args:
        response_content: Raw JSON string from the LLM.
        candidates: List of candidate transactions.

    Returns:
        dict: Contains analysis, decision, confidence, and transaction_id.
    """
    try:
        result = json.loads(response_content)

        # Ensure all required keys exist
        required_keys = ["analysis", "decision", "confidence", "transaction_id"]
        for key in required_keys:
            if key not in result:
                result[key] = None if key == "transaction_id" else ""

        # Heuristic override: if confidence is high but amount tolerance fails, lower it
        if result.get("transaction_id") is not None:
            best = next(
                (c for c in candidates if c["id"] == result["transaction_id"]),
                None,
            )
            if best and not amount_within_tolerance(
                candidates[0].get("invoice_amount", 0), best["amount"]
            ):
                result["confidence"] *= 0.5
                result["decision"] = AgentDecision.NEEDS_REVIEW

        return result

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM JSON response: {e}. Falling back to heuristic.")
        return _heuristic_fallback(candidates, candidates[0] if candidates else None)


def _heuristic_fallback(candidates: List[Dict], best_candidate: Optional[Dict]) -> Dict[str, Any]:
    """
    Deterministic fallback matching when LLM response cannot be parsed.

    Args:
        candidates: List of all candidate transactions.
        best_candidate: The best candidate (first in list) to evaluate.

    Returns:
        dict: Contains analysis, decision, confidence, and transaction_id.
    """
    if not best_candidate:
        return {
            "analysis": "No candidates available for heuristic fallback.",
            "confidence": DEFAULT_CONFIDENCE,
            "decision": AgentDecision.NO_MATCH,
            "transaction_id": None,
        }

    # Get invoice amount from the first candidate's context (passed via state)
    # Note: This is a simplification; ideally invoice amount would be passed separately.
    invoice_amount = candidates[0].get("invoice_amount", 0)

    amount_ok = amount_within_tolerance(invoice_amount, best_candidate["amount"])
    fuzzy_ok = best_candidate.get("similarity", 0) > SIMILARITY_THRESHOLD
    date_ok = calculate_date_diff(
        candidates[0].get("due_date", ""), best_candidate.get("date", "")
    ) <= DATE_TOLERANCE_DAYS

    if amount_ok and fuzzy_ok and date_ok:
        return {
            "analysis": "Heuristic fallback: amount, fuzzy, and date all match.",
            "confidence": HEURISTIC_CONFIDENCE_HIGH,
            "decision": AgentDecision.AUTO_MATCH,
            "transaction_id": best_candidate["id"],
        }
    elif amount_ok and fuzzy_ok:
        days_off = calculate_date_diff(
            candidates[0].get("due_date", ""), best_candidate.get("date", "")
        )
        return {
            "analysis": f"Heuristic: amount and fuzzy match but date off by {days_off} days.",
            "confidence": HEURISTIC_CONFIDENCE_MEDIUM,
            "decision": AgentDecision.NEEDS_REVIEW,
            "transaction_id": best_candidate["id"],
        }
    else:
        return {
            "analysis": (
                f"Heuristic: no sufficient match. "
                f"Best: {best_candidate.get('description', 'N/A')} "
                f"similarity {best_candidate.get('similarity', 0)}."
            ),
            "confidence": HEURISTIC_CONFIDENCE_LOW,
            "decision": AgentDecision.NO_MATCH,
            "transaction_id": None,
        }


# ----------------------------------------------------------------------
# Node functions
# ----------------------------------------------------------------------
def retrieve_node(state: ReconciliationState) -> Dict[str, Any]:
    """
    Retrieve invoice data and candidate transactions.

    This node fetches the invoice by ID and queries the database for
    potential matching transactions based on vendor and amount range.

    Args:
        state: Current reconciliation state (must contain invoice_id).

    Returns:
        dict: Updates to the state with invoice_data, candidates, and default values.
    """
    invoice_id = state["invoice_id"]
    logger.info(f"Retrieving data for invoice {invoice_id}")

    invoice_data = db_fetch_invoice(invoice_id)
    candidates = db_fetch_candidates(invoice_data)

    logger.info(f"Found {len(candidates)} candidate transactions for invoice {invoice_id}")

    return {
        "invoice_data": invoice_data,
        "candidates": candidates,
        "confidence": DEFAULT_CONFIDENCE,
        "decision": DEFAULT_DECISION,
        "notes": "",
        "analysis": DEFAULT_ANALYSIS,
        "transaction_id": None,
    }


def evaluate_node(state: ReconciliationState) -> Dict[str, Any]:
    """
    Evaluate candidates using an LLM to decide on the best match.

    This node sends the invoice and top candidates to the LLM with a structured prompt.
    The LLM returns a JSON response with analysis, decision, confidence, and transaction_id.
    If the LLM response is invalid, falls back to a deterministic heuristic.

    Args:
        state: Current reconciliation state (must contain invoice_data and candidates).

    Returns:
        dict: Updates to the state with analysis, decision, confidence, and transaction_id.
    """
    invoice = state["invoice_data"]
    candidates = state["candidates"]

    # If no candidates, return early
    if not candidates:
        logger.info("No candidates found – returning NO_MATCH")
        return {
            "analysis": "No candidates found within amount range.",
            "confidence": DEFAULT_CONFIDENCE,
            "decision": AgentDecision.NO_MATCH,
            "notes": "No transaction within ±20% of invoice amount.",
        }

    # Prepare top candidates for the LLM
    top_candidates = candidates[:CANDIDATE_LIMIT]
    # Add invoice amount to candidates for fallback heuristic
    for c in top_candidates:
        c["invoice_amount"] = invoice["amount"]

    prompt = f"""
You are a financial reconciliation expert. Given an invoice and potential bank transactions, decide which transaction (if any) matches.

Invoice:
- Vendor: {invoice['vendor']}
- Amount: {invoice['amount']} {invoice['currency']}
- Due Date: {invoice['due_date']}

Bank transactions (ID, Description, Amount, Date, Similarity score to vendor):
{json.dumps(top_candidates, default=str, indent=2)}

Instructions:
1. Consider amount proximity (allow small differences for fees/exchange).
2. Consider vendor name similarity (typos, abbreviations, acronyms).
3. Consider date proximity (allow up to 5 days grace).
4. Provide a short reasoning summary.
5. Based on your reasoning, suggest if it's a match, needs review, or no match.

Return your answer in the following JSON format:
{{
  "analysis": "your reasoning summary",
  "decision": "AUTO_MATCH" or "NEEDS_REVIEW" or "NO_MATCH",
  "confidence": 0.0-1.0,
  "transaction_id": <id of best match or null>
}}
Only return valid JSON.
"""

    try:
        # Invoke LLM
        logger.debug(f"Sending prompt to LLM for invoice {invoice.get('id', 'unknown')}")
        response = llm.invoke([
            SystemMessage(content="You are a strict but fair auditor."),
            HumanMessage(content=prompt)
        ])

        # Parse response with fallback
        result = _parse_llm_response(response.content, top_candidates)
        logger.info(f"LLM decision: {result.get('decision')} with confidence {result.get('confidence')}")

        return result

    except Exception as e:
        logger.error(f"LLM evaluation failed: {e}. Using fallback heuristic.")
        return _heuristic_fallback(top_candidates, top_candidates[0] if top_candidates else None)


def route_node(state: ReconciliationState) -> Dict[str, Any]:
    """
    Finalize the state and prepare notes for the match record.

    This node combines the analysis and decision into a human-readable notes field.

    Args:
        state: Current reconciliation state (must contain analysis, decision, transaction_id).

    Returns:
        dict: Updates to the state with notes, confidence, decision, and transaction_id.
    """
    analysis = state.get("analysis", "")
    decision = state.get("decision", AgentDecision.NO_MATCH)
    transaction_id = state.get("transaction_id")
    confidence = state.get("confidence", DEFAULT_CONFIDENCE)

    # Build notes from analysis and decision
    notes = analysis
    if decision == AgentDecision.AUTO_MATCH and transaction_id:
        notes += f" Match with transaction {transaction_id}."
    elif decision == AgentDecision.NEEDS_REVIEW:
        notes += " Needs human review."
    else:
        notes += " No match found."

    logger.debug(f"Route node: decision={decision}, confidence={confidence}, notes={notes[:100]}...")

    return {
        "notes": notes,
        "confidence": confidence,
        "decision": decision,
        "transaction_id": transaction_id,
    }