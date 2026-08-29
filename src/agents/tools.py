from sqlalchemy.orm import Session
from src.database import SessionLocal
from src.models.db_models import Invoice, Transaction
from src.utils.fuzzy import vendor_similarity, amount_within_tolerance
from datetime import datetime
from typing import List, Dict, Any

def db_fetch_invoice(invoice_id: int, db: Session = None) -> Dict[str, Any]:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if close_db:
        db.close()
    if not inv:
        return {}
    return {
        "id": inv.id,
        "vendor": inv.vendor,
        "amount": inv.amount,
        "currency": inv.currency,
        "due_date": inv.due_date,
        "invoice_number": inv.invoice_number
    }

def db_fetch_candidates(invoice_data: Dict[str, Any], db: Session = None) -> List[Dict[str, Any]]:
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True
    amount = invoice_data["amount"]
    vendor = invoice_data["vendor"]
    # Find transactions within ±20% of invoice amount
    candidates = db.query(Transaction).filter(
        Transaction.amount.between(amount * 0.8, amount * 1.2)
    ).limit(20).all()
    result = []
    for tx in candidates:
        sim = vendor_similarity(vendor, tx.description)
        result.append({
            "id": tx.id,
            "description": tx.description,
            "amount": tx.amount,
            "date": tx.date,
            "similarity": sim
        })
    if close_db:
        db.close()
    # Sort by similarity descending
    result.sort(key=lambda x: x["similarity"], reverse=True)
    return result

def calculate_date_diff(due_date: datetime, tx_date: datetime) -> int:
    return abs((due_date - tx_date).days)