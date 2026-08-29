from src.agents.graph import build_reconciliation_graph
from src.database import SessionLocal
from src.models.db_models import Match, Invoice
from sqlalchemy.orm import Session
from src.config import settings

app_graph = build_reconciliation_graph()

def run_advanced(invoice_id: int, db: Session) -> dict:
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        return {"status": "ERROR", "reason": "Invoice not found"}

    initial_state = {
        "invoice_id": invoice_id,
        "transaction_id": None,
        "invoice_data": {},
        "transaction_data": {},
        "candidates": [],
        "analysis": "",
        "confidence": 0.0,
        "decision": "NO_MATCH",
        "notes": ""
    }

    # Invoke graph
    final_state = app_graph.invoke(initial_state)

    # Persist match
    match = Match(
        invoice_id=invoice_id,
        transaction_id=final_state.get("transaction_id"),
        confidence_score=final_state["confidence"],
        agent_decision=final_state["decision"],
        agent_notes=final_state["notes"]
    )
    db.add(match)
    db.commit()
    db.refresh(match)

    # If auto-match, optionally update invoice status? (we'll keep status pending until human approves)
    return {"match_id": match.id, "decision": final_state["decision"], "confidence": final_state["confidence"], "transaction_id": final_state.get("transaction_id")}