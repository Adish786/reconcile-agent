from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import Optional
import csv
import io
from datetime import datetime
from src.database import SessionLocal, engine
from src.models.db_models import Base, Match, Invoice, Transaction
from src.models.schemas import ReconcileResponse, ReviewUpdateRequest, MatchOut
from src.services.baseline import run_baseline
from src.services.advanced import run_advanced
from src.config import settings
import logging

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

app = FastAPI(title="Reconcile Agent")

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/reconcile/{invoice_id}", response_model=ReconcileResponse)
def reconcile_invoice(
    invoice_id: int,
    use_advanced: bool = True,
    db: Session = Depends(get_db)
):
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not inv:
        raise HTTPException(404, "Invoice not found")

    if use_advanced:
        result = run_advanced(invoice_id, db)
    else:
        result = run_baseline(invoice_id, db)

    if result.get("status") == "ERROR":
        raise HTTPException(400, result["reason"])

    return ReconcileResponse(
        invoice_id=invoice_id,
        match_id=result.get("match_id", 0),
        decision=result.get("decision", result.get("status", "UNKNOWN")),
        confidence=result.get("confidence", 0.0)
    )

@app.get("/review/queue")
def get_review_queue(db: Session = Depends(get_db)):
    pending = db.query(Match).filter(
        Match.agent_decision == "NEEDS_REVIEW",
        Match.human_decision.is_(None)
    ).all()
    return {"pending_count": len(pending), "items": [MatchOut.model_validate(m) for m in pending]}

@app.put("/review/{match_id}")
def update_review(match_id: int, payload: ReviewUpdateRequest, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(404, "Match not found")

    if payload.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(400, "decision must be APPROVED or REJECTED")

    match.human_decision = payload.decision
    match.human_notes = payload.notes
    match.reviewed_at = datetime.utcnow()

    # If approved, mark invoice as paid
    if payload.decision == "APPROVED":
        invoice = db.query(Invoice).filter(Invoice.id == match.invoice_id).first()
        if invoice:
            invoice.status = "PAID"

    db.commit()
    return {"status": "updated", "match_id": match_id}

@app.post("/upload/invoices")
async def upload_invoices(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(400, "Only CSV files allowed")
    content = await file.read()
    try:
        reader = csv.DictReader(io.StringIO(content.decode('utf-8')))
        count = 0
        for row in reader:
            # Expect columns: vendor, invoice_number, amount, currency, due_date
            inv = Invoice(
                vendor=row['vendor'],
                invoice_number=row['invoice_number'],
                amount=float(row['amount']),
                currency=row.get('currency', 'USD'),
                due_date=datetime.strptime(row['due_date'], '%Y-%m-%d')
            )
            db.add(inv)
            count += 1
        db.commit()
        return {"message": f"Uploaded {count} invoices"}
    except Exception as e:
        raise HTTPException(400, f"Error parsing CSV: {str(e)}")