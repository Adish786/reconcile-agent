import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from src.agents.state import ReconciliationState
from src.agents.tools import db_fetch_invoice, db_fetch_candidates, calculate_date_diff
from src.utils.fuzzy import amount_within_tolerance
from src.config import settings
from typing import Dict, Any

llm = ChatOpenAI(
    model="gemini-1.5-flash", 
    temperature=0.1,
    openai_api_key=settings.OPENAI_API_KEY,
    openai_api_base=settings.OPENAI_BASE_URL
)
def retrieve_node(state: ReconciliationState) -> Dict[str, Any]:
    """Fetch invoice and candidate transactions."""
    invoice_id = state["invoice_id"]
    invoice_data = db_fetch_invoice(invoice_id)
    candidates = db_fetch_candidates(invoice_data)
    return {
        "invoice_data": invoice_data,
        "candidates": candidates,
        "confidence": 0.0,
        "decision": "NO_MATCH",
        "notes": "",
        "analysis": None,
        "transaction_id": None
    }

def evaluate_node(state: ReconciliationState) -> Dict[str, Any]:
    """Use LLM to reason about top candidates and produce a confidence score."""
    invoice = state["invoice_data"]
    candidates = state["candidates"]
    if not candidates:
        return {
            "analysis": "No candidates found within amount range.",
            "confidence": 0.0,
            "decision": "NO_MATCH",
            "notes": "No transaction within ±20% of invoice amount."
        }

    # Prepare a prompt for the LLM
    top_candidates = candidates[:5]  # limit to top 5 for reasoning
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
    response = llm.invoke([SystemMessage(content="You are a strict but fair auditor."),
                           HumanMessage(content=prompt)])
    try:
        result = json.loads(response.content)
        # Ensure we have all keys
        for key in ["analysis", "decision", "confidence", "transaction_id"]:
            if key not in result:
                result[key] = None if key == "transaction_id" else ""
        # Heuristic override: if confidence high but amount tolerance fails, lower it.
        if result["transaction_id"] is not None:
            # find the candidate
            best = next((c for c in candidates if c["id"] == result["transaction_id"]), None)
            if best:
                if not amount_within_tolerance(invoice["amount"], best["amount"]):
                    result["confidence"] *= 0.5
                    result["decision"] = "NEEDS_REVIEW"
        return result
    except json.JSONDecodeError:
        # Fallback to deterministic heuristic
        best = candidates[0]
        amount_ok = amount_within_tolerance(invoice["amount"], best["amount"])
        fuzzy_ok = best["similarity"] > 80
        date_ok = calculate_date_diff(invoice["due_date"], best["date"]) <= 5
        if amount_ok and fuzzy_ok and date_ok:
            return {
                "analysis": "Heuristic fallback: amount, fuzzy, and date all match.",
                "confidence": 0.95,
                "decision": "AUTO_MATCH",
                "transaction_id": best["id"]
            }
        elif amount_ok and fuzzy_ok:
            return {
                "analysis": f"Heuristic: amount and fuzzy match but date off by {calculate_date_diff(invoice['due_date'], best['date'])} days.",
                "confidence": 0.75,
                "decision": "NEEDS_REVIEW",
                "transaction_id": best["id"]
            }
        else:
            return {
                "analysis": f"Heuristic: no sufficient match. Best: {best['description']} similarity {best['similarity']}.",
                "confidence": 0.1,
                "decision": "NO_MATCH",
                "transaction_id": None
            }

def route_node(state: ReconciliationState) -> Dict[str, Any]:
    """Finalise the state, prepare notes."""
    # Ensure notes include the analysis
    notes = state.get("analysis", "")
    if state.get("decision") == "AUTO_MATCH" and state.get("transaction_id"):
        notes += f" Match with transaction {state['transaction_id']}"
    elif state.get("decision") == "NEEDS_REVIEW":
        notes += " Needs human review."
    else:
        notes += " No match found."
    return {"notes": notes, "confidence": state.get("confidence", 0.0), "decision": state.get("decision", "NO_MATCH"), "transaction_id": state.get("transaction_id")}