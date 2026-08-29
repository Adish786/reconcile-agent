"""
Unit tests for the baseline reconciliation service.

These tests verify the rule‑based matching logic in `run_baseline`.
The service matches invoices with transactions based on vendor name
and amount within a small tolerance (±0.01).
"""

import pytest
from datetime import datetime, timedelta

from src.models.db_models import Match, Transaction
from src.services.baseline import run_baseline


# ----------------------------------------------------------------------
# Tests for baseline reconciliation
# ----------------------------------------------------------------------
def test_baseline_match_found(db_session, seed_invoice, seed_transaction):
    """
    Test that a matching transaction is correctly identified.

    Given an invoice and a transaction with the same vendor and amount
    (within ±0.01), run_baseline should create a Match with AUTO_MATCH.
    """
    result = run_baseline(seed_invoice.id, db_session)

    # Check the result
    assert result["status"] == "AUTO_MATCH"
    assert result["decision"] == "APPROVED"
    assert result["confidence"] == 0.95
    assert result["match_id"] > 0

    # Verify the Match record in the database
    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.transaction_id == seed_transaction.id
    assert match.agent_decision == "AUTO_MATCH"
    assert match.confidence_score == 0.95
    assert "Baseline match" in match.agent_notes


def test_baseline_match_with_amount_tolerance(db_session, seed_invoice):
    """
    Test that amount tolerance (±0.01) works correctly.

    Create a transaction with amount = invoice.amount + 0.005 (rounded).
    """
    # Create a transaction with amount slightly different (within tolerance)
    tx = Transaction(
        date=datetime.now(),
        description="Acme Corp payment",
        amount=seed_invoice.amount + 0.005,  # within ±0.01
        currency="USD",
        vendor=seed_invoice.vendor,
    )
    db_session.add(tx)
    db_session.commit()

    result = run_baseline(seed_invoice.id, db_session)
    assert result["status"] == "AUTO_MATCH"
    assert result["confidence"] == 0.95

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.transaction_id == tx.id


def test_baseline_no_match(db_session, seed_invoice):
    """
    Test that no match is created when no transaction exists.
    """
    # No transaction seeded
    result = run_baseline(seed_invoice.id, db_session)
    assert result["status"] == "NO_MATCH"
    assert result["decision"] == "NO_MATCH"
    assert result["confidence"] == 0.0
    assert result["match_id"] > 0  # A Match record is still created

    # Verify the Match record in the database
    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.transaction_id is None
    assert match.agent_decision == "NO_MATCH"
    assert match.confidence_score == 0.0
    assert "No transaction found" in match.agent_notes


def test_baseline_no_match_different_vendor(db_session, seed_invoice):
    """
    Test that a transaction with a different vendor is not matched.
    """
    tx = Transaction(
        date=datetime.now(),
        description="Beta Inc payment",
        amount=seed_invoice.amount,
        currency="USD",
        vendor="Beta Inc",
    )
    db_session.add(tx)
    db_session.commit()

    result = run_baseline(seed_invoice.id, db_session)
    assert result["status"] == "NO_MATCH"
    assert result["decision"] == "NO_MATCH"

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.transaction_id is None
    assert match.agent_decision == "NO_MATCH"


def test_baseline_no_match_amount_out_of_tolerance(db_session, seed_invoice):
    """
    Test that a transaction with the same vendor but amount outside tolerance is not matched.
    """
    tx = Transaction(
        date=datetime.now(),
        description="Acme Corp payment",
        amount=seed_invoice.amount + 0.02,  # > 0.01 tolerance
        currency="USD",
        vendor=seed_invoice.vendor,
    )
    db_session.add(tx)
    db_session.commit()

    result = run_baseline(seed_invoice.id, db_session)
    assert result["status"] == "NO_MATCH"
    assert result["decision"] == "NO_MATCH"

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.transaction_id is None
    assert match.agent_decision == "NO_MATCH"


def test_baseline_invoice_not_found(db_session):
    """
    Test that run_baseline returns an error when the invoice does not exist.
    """
    result = run_baseline(999, db_session)
    assert result["status"] == "ERROR"
    assert "Invoice not found" in result["reason"]
    assert result["decision"] == "NO_MATCH"
    assert result["confidence"] == 0.0
    assert result["match_id"] == 0

    # No Match record should be created
    match = db_session.query(Match).filter_by(invoice_id=999).first()
    assert match is None


def test_baseline_multiple_candidates(db_session, seed_invoice):
    """
    Test that the first matching transaction (by query order) is selected.

    Since we limit to the first candidate, we should check that the best
    candidate is chosen (if we had ordering logic).
    """
    # Add two transactions with same vendor and amount (both match)
    tx1 = Transaction(
        date=datetime.now(),
        description="Acme Corp payment 1",
        amount=seed_invoice.amount,
        currency="USD",
        vendor=seed_invoice.vendor,
    )
    tx2 = Transaction(
        date=datetime.now(),
        description="Acme Corp payment 2",
        amount=seed_invoice.amount,
        currency="USD",
        vendor=seed_invoice.vendor,
    )
    db_session.add_all([tx1, tx2])
    db_session.commit()

    result = run_baseline(seed_invoice.id, db_session)
    assert result["status"] == "AUTO_MATCH"

    # The match should reference the first transaction (by insertion order)
    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match.transaction_id == tx1.id  # SQLite returns in insertion order

    # Note: In a real system, you might want to pick the best candidate
    # (e.g., closest amount), but the current implementation picks the first.
    # This test documents that behavior.


def test_baseline_match_creates_match_record(db_session, seed_invoice, seed_transaction):
    """
    Test that a Match record is created with all required fields.
    """
    result = run_baseline(seed_invoice.id, db_session)

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.invoice_id == seed_invoice.id
    assert match.transaction_id == seed_transaction.id
    assert match.confidence_score == 0.95
    assert match.agent_decision == "AUTO_MATCH"
    assert match.agent_notes is not None
    assert match.human_decision is None
    assert match.human_notes is None
    assert match.reviewed_at is None
    assert match.created_at is not None


def test_baseline_no_match_creates_match_record(db_session, seed_invoice):
    """
    Test that even when no match is found, a Match record is created with NO_MATCH.
    This ensures that every reconciliation is audited.
    """
    result = run_baseline(seed_invoice.id, db_session)

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.invoice_id == seed_invoice.id
    assert match.transaction_id is None
    assert match.confidence_score == 0.0
    assert match.agent_decision == "NO_MATCH"
    assert match.agent_notes is not None
    assert match.created_at is not None
    assert match.human_decision is None
    assert match.human_notes is None
    assert match.reviewed_at is None