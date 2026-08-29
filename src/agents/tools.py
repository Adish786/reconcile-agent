"""
Database utility functions for the reconciliation agent.

This module provides helper functions to fetch invoice data and candidate transactions,
as well as utilities for date difference calculations.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session
from datetime import datetime
from src.database import SessionLocal
from src.models.db_models import Invoice, Transaction
from src.utils.fuzzy import amount_within_tolerance, vendor_similarity

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
AMOUNT_TOLERANCE_FACTOR = 0.2  # ±20% for candidate filtering
MAX_CANDIDATES = 20            # Maximum number of candidates to return
SIMILARITY_SORT_REVERSE = True # Sort by similarity descending


# ----------------------------------------------------------------------
# Helper: Manage database sessions
# ----------------------------------------------------------------------
def _get_session(db: Optional[Session]) -> tuple[Session, bool]:
    """
    Get a database session and a flag indicating whether it was created here.

    Args:
        db: Optional existing session.

    Returns:
        tuple: (Session, bool) – the session and True if it was newly created.
    """
    if db is not None:
        return db, False
    return SessionLocal(), True


def _close_session_if_created(db: Session, created: bool) -> None:
    """
    Close the session if it was created within this function.

    Args:
        db: The session to possibly close.
        created: Flag indicating whether the session was created locally.
    """
    if created:
        db.close()


# ----------------------------------------------------------------------
# Public functions
# ----------------------------------------------------------------------
def db_fetch_invoice(invoice_id: int, db: Optional[Session] = None) -> Dict[str, Any]:
    """
    Fetch invoice data from the database by ID.

    Args:
        invoice_id: Primary key of the invoice.
        db: Optional existing SQLAlchemy session. If not provided, a new one is created.

    Returns:
        dict: Invoice fields (id, vendor, amount, currency, due_date, invoice_number).
              Returns an empty dict if the invoice is not found.

    Example:
        >>> invoice = db_fetch_invoice(123)
        >>> print(invoice.get('vendor'))
        'Acme Corp'
    """
    session, created = _get_session(db)
    try:
        invoice = session.query(Invoice).filter(Invoice.id == invoice_id).first()
        if not invoice:
            logger.warning(f"Invoice {invoice_id} not found")
            return {}
        return {
            "id": invoice.id,
            "vendor": invoice.vendor,
            "amount": invoice.amount,
            "currency": invoice.currency,
            "due_date": invoice.due_date,
            "invoice_number": invoice.invoice_number,
        }
    except Exception as e:
        logger.error(f"Error fetching invoice {invoice_id}: {str(e)}", exc_info=True)
        return {}
    finally:
        _close_session_if_created(session, created)


def db_fetch_candidates(invoice_data: Dict[str, Any], db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Fetch candidate transactions that are within ±20% of the invoice amount.

    The candidates are sorted by similarity (vendor name similarity to transaction description).

    Args:
        invoice_data: Dictionary containing at least 'amount' and 'vendor'.
        db: Optional existing SQLAlchemy session.

    Returns:
        List[Dict[str, Any]]: List of candidate transactions, each with:
            - id: Transaction ID
            - description: Transaction description
            - amount: Transaction amount
            - date: Transaction date
            - similarity: Similarity score (0-100) between invoice vendor and transaction description.
            - invoice_amount: (added for fallback in evaluation) – the original invoice amount.

    Example:
        >>> invoice = db_fetch_invoice(123)
        >>> candidates = db_fetch_candidates(invoice)
        >>> for c in candidates[:3]:
        ...     print(c['description'], c['similarity'])
    """
    if not invoice_data:
        logger.warning("Empty invoice data provided to db_fetch_candidates")
        return []

    amount = invoice_data["amount"]
    vendor = invoice_data["vendor"]

    session, created = _get_session(db)
    try:
        # Query transactions within ±20% of invoice amount
        candidates_query = session.query(Transaction).filter(
            Transaction.amount.between(
                amount * (1 - AMOUNT_TOLERANCE_FACTOR),
                amount * (1 + AMOUNT_TOLERANCE_FACTOR),
            )
        ).limit(MAX_CANDIDATES).all()

        logger.debug(f"Found {len(candidates_query)} candidates for invoice amount {amount}")

        # Build result list with similarity scores
        result = []
        for tx in candidates_query:
            sim = vendor_similarity(vendor, tx.description)
            result.append({
                "id": tx.id,
                "description": tx.description,
                "amount": tx.amount,
                "date": tx.date,
                "similarity": sim,
                "invoice_amount": amount,  # Add for fallback in evaluate_node
            })

        # Sort by similarity descending (highest first)
        result.sort(key=lambda x: x["similarity"], reverse=SIMILARITY_SORT_REVERSE)
        return result

    except Exception as e:
        logger.error(f"Error fetching candidates: {str(e)}", exc_info=True)
        return []
    finally:
        _close_session_if_created(session, created)


def calculate_date_diff(due_date, tx_date) -> int:
    """
    Calculate absolute days difference between two dates.
    Handles datetime objects, ISO format strings, and None/empty values.
    Returns 999 if either date is invalid or missing.
    """
    def parse_date(date_val):
        if date_val is None:
            return None
        if isinstance(date_val, datetime):
            return date_val
        if isinstance(date_val, str):
            date_val = date_val.strip()
            if date_val == '':
                return None
            try:
                return datetime.fromisoformat(date_val)
            except ValueError:
                return None
        return None

    d1 = parse_date(due_date)
    d2 = parse_date(tx_date)
    if d1 is None or d2 is None:
        return 999  # large number so it fails any reasonable tolerance
    return abs((d1 - d2).days)