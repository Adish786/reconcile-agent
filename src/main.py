"""
FastAPI application for invoice-to-bank reconciliation using agentic workflows.

This module provides REST endpoints for:
- Uploading invoice CSV files.
- Performing reconciliation (baseline rule-based or advanced LLM-based).
- Managing a human review queue for ambiguous matches.
"""

import csv
import io
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from src.config import settings
from src.database import SessionLocal, engine
from src.models.db_models import Base, Invoice, InvoiceStatus, Match, Transaction
from src.models.schemas import MatchOut, ReconcileResponse, ReviewUpdateRequest
from src.services.advanced import run_advanced
from src.services.baseline import run_baseline

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------
logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------
app = FastAPI(
    title="Reconcile Agent",
    description="Agentic invoice-to-bank reconciliation with human-in-the-loop",
    version="0.1.0",
)

# ----------------------------------------------------------------------
# Database lifecycle
# ----------------------------------------------------------------------
@app.on_event("startup")
def startup():
    """Create database tables on application startup."""
    logger.info("Creating database tables if they don't exist...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables ready.")


@app.on_event("shutdown")
def shutdown():
    """Perform any cleanup on shutdown."""
    logger.info("Shutting down application.")


# ----------------------------------------------------------------------
# Dependency: Database session
# ----------------------------------------------------------------------
def get_db() -> Session:
    """
    Provide a SQLAlchemy database session.

    Yields:
        Session: A database session that is closed after the request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------------------------------------------------------------
# Helper: Parse CSV content into Invoice objects
# ----------------------------------------------------------------------
def _parse_invoice_csv(content: bytes) -> List[dict]:
    """
    Parse CSV content into a list of invoice dictionaries.

    Args:
        content: Raw bytes of the CSV file.

    Returns:
        List[dict]: Each dict contains keys: vendor, invoice_number, amount, currency, due_date.

    Raises:
        ValueError: If required columns are missing or data is malformed.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError("File must be UTF-8 encoded.") from e

    try:
        reader = csv.DictReader(io.StringIO(text))
    except Exception as e:
        raise ValueError(f"Invalid CSV format: {str(e)}") from e

    required_columns = {"vendor", "invoice_number", "amount", "due_date"}
    if not required_columns.issubset(reader.fieldnames or set()):
        missing = required_columns - set(reader.fieldnames or set())
        raise ValueError(f"Missing required columns: {missing}")

    invoices = []
    for row_num, row in enumerate(reader, start=2):  # row 1 is header
        try:
            invoice = {
                "vendor": row["vendor"].strip(),
                "invoice_number": row["invoice_number"].strip(),
                "amount": float(row["amount"]),
                "currency": row.get("currency", "USD").strip(),
                "due_date": datetime.strptime(row["due_date"], "%Y-%m-%d"),
            }
            invoices.append(invoice)
        except (KeyError, ValueError) as e:
            raise ValueError(f"Row {row_num}: {str(e)}") from e

    return invoices


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@app.post("/upload/invoices", summary="Upload invoices from CSV")
async def upload_invoices(
    file: UploadFile = File(..., description="CSV file with columns: vendor, invoice_number, amount, currency, due_date"),
    db: Session = Depends(get_db),
):
    """
    Upload a CSV file containing invoice data.

    Expected CSV columns:
        - vendor (str)
        - invoice_number (str, unique)
        - amount (float)
        - currency (str, defaults to USD)
        - due_date (YYYY-MM-DD)

    Returns:
        dict: {"message": f"Uploaded {count} invoices"}
    """
    # Validate file type
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(400, "Only CSV files are allowed.")

    # Read and parse file
    content = await file.read()
    try:
        invoice_data = _parse_invoice_csv(content)
    except ValueError as e:
        logger.warning(f"CSV parsing failed: {e}")
        raise HTTPException(400, f"CSV parsing error: {str(e)}")

    # Insert into database
    try:
        count = 0
        for data in invoice_data:
            inv = Invoice(
                vendor=data["vendor"],
                invoice_number=data["invoice_number"],
                amount=data["amount"],
                currency=data["currency"],
                due_date=data["due_date"],
            )
            db.add(inv)
            count += 1
        db.commit()
        logger.info(f"Uploaded {count} invoices.")
        return {"message": f"Uploaded {count} invoices"}

    except Exception as e:
        db.rollback()
        logger.error(f"Database error during upload: {e}")
        raise HTTPException(500, "Internal server error while saving invoices.")


@app.post("/reconcile/{invoice_id}", response_model=ReconcileResponse)
def reconcile_invoice(
    invoice_id: int,
    use_advanced: bool = True,
    db: Session = Depends(get_db),
):
    """
    Reconcile a specific invoice against bank transactions.

    Args:
        invoice_id: The primary key of the invoice to reconcile.
        use_advanced: If True, uses the LLM-based agent; otherwise uses the rule-based baseline.
        db: Database session.

    Returns:
        ReconcileResponse: Contains invoice_id, match_id, decision, and confidence.

    Raises:
        HTTPException(404): If invoice not found.
        HTTPException(400): If the reconciliation service returns an error.
    """
    # Fetch invoice
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        logger.warning(f"Invoice {invoice_id} not found.")
        raise HTTPException(404, "Invoice not found")

    # Run reconciliation
    try:
        if use_advanced:
            result = run_advanced(invoice_id, db)
        else:
            result = run_baseline(invoice_id, db)
    except Exception as e:
        logger.error(f"Reconciliation failed for invoice {invoice_id}: {e}")
        raise HTTPException(500, f"Reconciliation service error: {str(e)}")

    # Check for service-level errors
    if result.get("status") == "ERROR":
        raise HTTPException(400, result.get("reason", "Unknown error"))

    # Build response
    return ReconcileResponse(
        invoice_id=invoice_id,
        match_id=result.get("match_id", 0),
        decision=result.get("decision", result.get("status", "UNKNOWN")),
        confidence=result.get("confidence", 0.0),
    )


@app.get("/review/queue", summary="Get pending review items")
def get_review_queue(db: Session = Depends(get_db)):
    """
    Retrieve all matches that require human review.

    Returns:
        dict: Contains pending_count and list of matches (as MatchOut).
    """
    pending = (
        db.query(Match)
        .filter(
            Match.agent_decision == "NEEDS_REVIEW",
            Match.human_decision.is_(None),
        )
        .all()
    )
    return {
        "pending_count": len(pending),
        "items": [MatchOut.model_validate(m) for m in pending],
    }


@app.put("/review/{match_id}", summary="Approve or reject a match")
def update_review(
    match_id: int,
    payload: ReviewUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    Update a match with a human decision.

    Args:
        match_id: The primary key of the match.
        payload: Contains 'decision' (APPROVED or REJECTED) and optional 'notes'.

    Returns:
        dict: Status update confirmation.

    Raises:
        HTTPException(404): If match not found.
        HTTPException(400): If decision is invalid.
    """
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        logger.warning(f"Match {match_id} not found.")
        raise HTTPException(404, "Match not found")

    if payload.decision not in ("APPROVED", "REJECTED"):
        raise HTTPException(400, "decision must be APPROVED or REJECTED")

    # Update match
    match.human_decision = payload.decision
    match.human_notes = payload.notes
    match.reviewed_at = datetime.utcnow()

    # If approved, mark the invoice as paid
    if payload.decision == "APPROVED":
        invoice = db.query(Invoice).filter(Invoice.id == match.invoice_id).first()
        if invoice:
            invoice.status = InvoiceStatus.PAID
           # invoice.status = "PAID"
            logger.info(f"Invoice {invoice.id} marked as PAID.")

    db.commit()
    logger.info(f"Match {match_id} updated with decision: {payload.decision}")
    return {"status": "updated", "match_id": match_id}


# ----------------------------------------------------------------------
# Health check endpoint (useful for monitoring)
# ----------------------------------------------------------------------
@app.get("/health", summary="Health check")
def health_check():
    """
    Simple health check endpoint.

    Returns:
        dict: {"status": "ok"}
    """
    return {"status": "ok"}