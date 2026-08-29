from src.models.db_models import Invoice, Transaction, Match
from sqlalchemy.orm import Session
from datetime import datetime

def run_baseline(invoice_id: int, db: Session):
    # Get the invoice
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        return {"status": "ERROR", "reason": "Invoice not found"}

    # Look for a transaction with same vendor and close amount
    # We allow a 0.01 difference to handle rounding
    candidate = db.query(Transaction).filter(
        Transaction.vendor == invoice.vendor,
        Transaction.amount.between(invoice.amount - 0.01, invoice.amount + 0.01)
    ).first()

    if candidate:
        # Create a Match record
        match = Match(
            invoice_id=invoice.id,
            transaction_id=candidate.id,
            confidence_score=0.95,
            agent_decision="AUTO_MATCH",
            agent_notes=f"Baseline match: vendor {invoice.vendor}, amount {invoice.amount}",
            created_at=datetime.utcnow()
        )
        db.add(match)
        db.commit()
        db.refresh(match)
        return {
            "status": "AUTO_MATCH",
            "decision": "APPROVED",
            "confidence": 0.95,
            "match_id": match.id
        }
    else:
        # No match found – create a NO_MATCH record (so it appears in review queue if needed)
        match = Match(
            invoice_id=invoice.id,
            transaction_id=None,
            confidence_score=0.0,
            agent_decision="NO_MATCH",
            agent_notes=f"No transaction found for vendor {invoice.vendor}, amount {invoice.amount}",
            created_at=datetime.utcnow()
        )
        db.add(match)
        db.commit()
        db.refresh(match)
        return {
            "status": "NO_MATCH",
            "decision": "NO_MATCH",
            "confidence": 0.0,
            "match_id": match.id
        }