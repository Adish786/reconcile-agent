"""
Integration tests for the Reconcile Agent API endpoints.

Each test runs with a clean database (all tables truncated before each test).
"""

import csv
import io
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, SessionLocal, get_db
from src.main import app
from src.models.db_models import Invoice, Match, Transaction
from src.models.schemas import AgentDecision, HumanDecision
from src.models.db_models import InvoiceStatus

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="function")
def db_session():
    """
    Provide a clean database session by truncating all tables before each test.
    """
    # Use the default SessionLocal (which uses the global engine)
    session = SessionLocal()

    # Delete all data from all tables in reverse order to avoid FK constraints
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()

    # Override get_db to return this session
    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    yield session

    # Clean up
    session.rollback()
    session.close()
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(db_session):
    """Provide a FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def seed_invoice(db_session):
    """Create a test invoice."""
    inv = Invoice(
        vendor="Acme Corp",
        invoice_number="INV-001",
        amount=100.0,
        currency="USD",
        due_date=datetime.now() + timedelta(days=5),
    )
    db_session.add(inv)
    db_session.commit()
    db_session.refresh(inv)
    return inv


@pytest.fixture
def seed_transaction(db_session):
    """Create a test transaction that matches the seed invoice."""
    tx = Transaction(
        date=datetime.now(),
        description="Acme Corp payment",
        amount=100.0,
        currency="USD",
        vendor="Acme Corp",
    )
    db_session.add(tx)
    db_session.commit()
    db_session.refresh(tx)
    return tx


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def create_csv_content(rows):
    """Create CSV content as bytes."""
    output = io.StringIO()
    fieldnames = ["vendor", "invoice_number", "amount", "currency", "due_date"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def create_transaction_csv_content(rows):
    """Create CSV content for transactions as bytes."""
    output = io.StringIO()
    fieldnames = ["vendor", "amount", "date", "currency", "description"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


# ----------------------------------------------------------------------
# Tests for /upload/invoices
# ----------------------------------------------------------------------
def test_upload_invoices_success(client, db_session):
    """Test successful CSV upload."""
    csv_data = create_csv_content([
        {
            "vendor": "Test Corp",
            "invoice_number": "INV-UNIQUE-001",
            "amount": "200.00",
            "currency": "USD",
            "due_date": "2026-12-31",
        }
    ])
    response = client.post(
        "/upload/invoices",
        files={"file": ("invoices.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 200
    assert response.json() == {"message": "Uploaded 1 invoices"}

    invoice = db_session.query(Invoice).filter_by(invoice_number="INV-UNIQUE-001").first()
    assert invoice is not None
    assert invoice.vendor == "Test Corp"


def test_upload_invoices_missing_columns(client):
    """Test CSV with missing required columns."""
    csv_data = b"vendor,amount\nTest Corp,100.00\n"
    response = client.post(
        "/upload/invoices",
        files={"file": ("invoices.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 400
    assert "Missing required columns" in response.json()["detail"]


def test_upload_invoices_invalid_date(client):
    """Test CSV with invalid date format."""
    csv_data = create_csv_content([
        {
            "vendor": "Test Corp",
            "invoice_number": "INV-003",
            "amount": "300.00",
            "currency": "USD",
            "due_date": "invalid-date",
        }
    ])
    response = client.post(
        "/upload/invoices",
        files={"file": ("invoices.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 400
    assert "does not match format" in response.json()["detail"].lower()


def test_upload_invoices_duplicate_invoice_number(client, db_session):
    """Test CSV with a duplicate invoice number (should fail)."""
    unique_number = "INV-UNIQUE-002"
    csv_data = create_csv_content([
        {
            "vendor": "Test Corp",
            "invoice_number": unique_number,
            "amount": "100.00",
            "currency": "USD",
            "due_date": "2026-12-31",
        }
    ])
    # First upload – should succeed
    response = client.post(
        "/upload/invoices",
        files={"file": ("invoices.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 200

    # Second upload – should fail with 500 (integrity error)
    response = client.post(
        "/upload/invoices",
        files={"file": ("invoices.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 500
    assert "Internal server error" in response.json()["detail"]


# ----------------------------------------------------------------------
# Tests for /reconcile/ (baseline)
# ----------------------------------------------------------------------
def test_baseline_match(client, db_session, seed_invoice, seed_transaction):
    """Test baseline reconciliation with a matching transaction."""
    response = client.post(f"/reconcile/{seed_invoice.id}?use_advanced=false")
    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == seed_invoice.id
    assert data["decision"] == "APPROVED"
    assert data["confidence"] == 0.95
    assert data["match_id"] > 0

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.AUTO_MATCH
    assert match.transaction_id == seed_transaction.id


def test_baseline_no_match(client, db_session):
    """Test baseline reconciliation when no transaction matches."""
    invoice = Invoice(
        vendor="NoMatch Corp",
        invoice_number="INV-NOMATCH",
        amount=999.99,
        currency="USD",
        due_date=datetime.now() + timedelta(days=5),
    )
    db_session.add(invoice)
    db_session.commit()
    db_session.refresh(invoice)

    response = client.post(f"/reconcile/{invoice.id}?use_advanced=false")
    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == invoice.id
    assert data["decision"] == "NO_MATCH"
    assert data["confidence"] == 0.0

    match = db_session.query(Match).filter_by(invoice_id=invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.NO_MATCH
    assert match.transaction_id is None


def test_reconcile_invoice_not_found(client):
    """Test reconciliation with non-existent invoice."""
    response = client.post("/reconcile/999?use_advanced=false")
    assert response.status_code == 404
    assert "Invoice not found" in response.json()["detail"]


# ----------------------------------------------------------------------
# Tests for /reconcile/ (advanced)
# ----------------------------------------------------------------------
@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_auto_match(mock_build_graph, client, db_session, seed_invoice, seed_transaction):
    """Test advanced reconciliation with AUTO_MATCH."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.AUTO_MATCH,
        "confidence": 0.95,
        "transaction_id": seed_transaction.id,
        "notes": "Exact match found",
        "analysis": "Invoice matches transaction perfectly",
    }
    mock_build_graph.return_value = mock_graph

    response = client.post(f"/reconcile/{seed_invoice.id}?use_advanced=true")
    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == seed_invoice.id
    assert data["decision"] == AgentDecision.AUTO_MATCH
    assert data["confidence"] == 0.95

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.AUTO_MATCH


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_needs_review(mock_build_graph, client, db_session, seed_invoice):
    """Test advanced reconciliation with NEEDS_REVIEW."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.NEEDS_REVIEW,
        "confidence": 0.55,
        "transaction_id": None,
        "notes": "Uncertain match, needs human review",
        "analysis": "Vendor matches but amount differs",
    }
    mock_build_graph.return_value = mock_graph

    response = client.post(f"/reconcile/{seed_invoice.id}?use_advanced=true")
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == AgentDecision.NEEDS_REVIEW
    assert data["confidence"] == 0.55

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.NEEDS_REVIEW


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_no_match(mock_build_graph, client, db_session, seed_invoice):
    """Test advanced reconciliation with NO_MATCH."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.NO_MATCH,
        "confidence": 0.0,
        "transaction_id": None,
        "notes": "No matching transaction found",
        "analysis": "None of the candidates match",
    }
    mock_build_graph.return_value = mock_graph

    response = client.post(f"/reconcile/{seed_invoice.id}?use_advanced=true")
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == AgentDecision.NO_MATCH
    assert data["confidence"] == 0.0


# ----------------------------------------------------------------------
# Tests for /review/queue
# ----------------------------------------------------------------------
def test_review_queue_empty(client, db_session):
    """Test review queue with no pending matches."""
    # Ensure no matches exist (already truncated at fixture start)
    response = client.get("/review/queue")
    assert response.status_code == 200
    data = response.json()
    assert data["pending_count"] == 0
    assert data["items"] == []


def test_review_queue_with_pending(client, db_session, seed_invoice, seed_transaction):
    """Test review queue with a pending match."""
    # Directly create a match
    match = Match(
        invoice_id=seed_invoice.id,
        transaction_id=seed_transaction.id,
        confidence_score=0.6,
        agent_decision=AgentDecision.NEEDS_REVIEW,
        agent_notes="Need human review",
        created_at=datetime.utcnow(),
    )
    db_session.add(match)
    db_session.commit()

    response = client.get("/review/queue")
    assert response.status_code == 200
    data = response.json()
    assert data["pending_count"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == match.id


# ----------------------------------------------------------------------
# Tests for /review/{match_id}
# ----------------------------------------------------------------------
def test_review_update_approve(client, db_session, seed_invoice):
    """Test approving a match."""
    match = Match(
        invoice_id=seed_invoice.id,
        transaction_id=None,
        confidence_score=0.5,
        agent_decision=AgentDecision.NEEDS_REVIEW,
        agent_notes="Test review",
        created_at=datetime.utcnow(),
    )
    db_session.add(match)
    db_session.commit()

    payload = {"decision": "APPROVED", "notes": "Looks good"}
    response = client.put(f"/review/{match.id}", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "updated", "match_id": match.id}

    db_session.refresh(match)
    assert match.human_decision == HumanDecision.APPROVED
    assert match.human_notes == "Looks good"
    assert match.reviewed_at is not None

    invoice = db_session.query(Invoice).filter_by(id=seed_invoice.id).first()
    assert invoice.status == InvoiceStatus.PAID


def test_review_update_reject(client, db_session, seed_invoice):
    """Test rejecting a match."""
    match = Match(
        invoice_id=seed_invoice.id,
        transaction_id=None,
        confidence_score=0.5,
        agent_decision=AgentDecision.NEEDS_REVIEW,
        agent_notes="Test",
        created_at=datetime.utcnow(),
    )
    db_session.add(match)
    db_session.commit()

    payload = {"decision": "REJECTED", "notes": "Not a match"}
    response = client.put(f"/review/{match.id}", json=payload)
    assert response.status_code == 200
    db_session.refresh(match)
    assert match.human_decision == HumanDecision.REJECTED

    invoice = db_session.query(Invoice).filter_by(id=seed_invoice.id).first()
    assert invoice.status == InvoiceStatus.PENDING


def test_review_update_invalid_decision(client, db_session, seed_invoice):
    """Test review update with invalid decision."""
    match = Match(
        invoice_id=seed_invoice.id,
        transaction_id=None,
        confidence_score=0.5,
        agent_decision=AgentDecision.NEEDS_REVIEW,
        agent_notes="Test",
        created_at=datetime.utcnow(),
    )
    db_session.add(match)
    db_session.commit()

    payload = {"decision": "INVALID", "notes": "Test"}
    response = client.put(f"/review/{match.id}", json=payload)
    # Pydantic validation (Enum) rejects invalid values -> 422
    assert response.status_code == 422
    assert "decision" in str(response.json())


def test_review_update_match_not_found(client):
    """Test review update for non-existent match."""
    payload = {"decision": "APPROVED", "notes": "Test"}
    response = client.put("/review/999", json=payload)
    assert response.status_code == 404
    assert "Match not found" in response.json()["detail"]


# ----------------------------------------------------------------------
# Tests for /health
# ----------------------------------------------------------------------
def test_health_check(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ----------------------------------------------------------------------
# Tests for /upload/transactions
# ----------------------------------------------------------------------
def test_upload_transactions_success(client, db_session):
    """Test successful transaction CSV upload."""
    csv_data = create_transaction_csv_content([
        {
            "vendor": "Bank Corp",
            "amount": "150.50",
            "date": "2026-08-31",
            "currency": "USD",
            "description": "Payment received",
        }
    ])
    response = client.post(
        "/upload/transactions",
        files={"file": ("transactions.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 200
    assert response.json() == {"message": "Uploaded 1 transactions"}

    txn = db_session.query(Transaction).filter_by(vendor="Bank Corp", amount=150.50).first()
    assert txn is not None
    assert txn.date.strftime("%Y-%m-%d") == "2026-08-31"


def test_upload_transactions_missing_columns(client):
    """Test transaction CSV with missing required columns."""
    csv_data = b"vendor,amount\nBank Corp,100.00\n"
    response = client.post(
        "/upload/transactions",
        files={"file": ("transactions.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 400
    assert "Missing required columns" in response.json()["detail"]


def test_upload_transactions_invalid_date(client):
    """Test transaction CSV with invalid date format."""
    csv_data = create_transaction_csv_content([
        {
            "vendor": "Bank Corp",
            "amount": "200.00",
            "date": "invalid-date",
            "currency": "USD",
            "description": "Test",
        }
    ])
    response = client.post(
        "/upload/transactions",
        files={"file": ("transactions.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 400
    assert "does not match format" in response.json()["detail"].lower()


def test_upload_transactions_clear_flag(client, db_session):
    """Test that clear=True deletes existing transactions before upload."""
    # Insert a transaction directly
    old_txn = Transaction(
        vendor="Old Vendor",
        amount=50.0,
        date=datetime.now(),
        currency="USD",
        description="Old",
    )
    db_session.add(old_txn)
    db_session.commit()

    # Upload new transactions with clear=True
    csv_data = create_transaction_csv_content([
        {
            "vendor": "New Vendor",
            "amount": "75.00",
            "date": "2026-08-31",
            "currency": "USD",
            "description": "New",
        }
    ])
    response = client.post(
        "/upload/transactions?clear=true",
        files={"file": ("transactions.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 200

    # Old transaction should be gone
    old_count = db_session.query(Transaction).filter_by(vendor="Old Vendor").count()
    assert old_count == 0
    # New transaction should exist
    new_txn = db_session.query(Transaction).filter_by(vendor="New Vendor").first()
    assert new_txn is not None


def test_upload_transactions_clear_with_matches(client, db_session, seed_invoice, seed_transaction):
    """
    Test that clear=True deletes transactions and also removes associated matches
    to avoid foreign key violations.
    """
    # Create a match referencing the seed transaction
    match = Match(
        invoice_id=seed_invoice.id,
        transaction_id=seed_transaction.id,
        confidence_score=0.9,
        agent_decision=AgentDecision.AUTO_MATCH,
        agent_notes="Test",
        created_at=datetime.utcnow(),
    )
    db_session.add(match)
    db_session.commit()
    match_id = match.id  # Save the ID before the match is deleted

    # Upload a new transaction with clear=True
    csv_data = create_transaction_csv_content([
        {
            "vendor": "Another Vendor",
            "amount": "100.00",
            "date": "2026-08-31",
            "currency": "USD",
            "description": "New",
        }
    ])
    response = client.post(
        "/upload/transactions?clear=true",
        files={"file": ("transactions.csv", csv_data, "text/csv")},
    )
    assert response.status_code == 200

    # The original transaction (vendor "Acme Corp") should be deleted
    assert db_session.query(Transaction).filter_by(
        vendor="Acme Corp", amount=100.0
    ).count() == 0

    # The match referencing it should also be gone (use the saved ID)
    assert db_session.query(Match).filter_by(id=match_id).count() == 0

    
def test_upload_transactions_invalid_file_type(client):
    """Test that only CSV files are accepted."""
    response = client.post(
        "/upload/transactions",
        files={"file": ("transactions.txt", b"invalid", "text/plain")},
    )
    assert response.status_code == 400
    assert "Only CSV files are allowed" in response.json()["detail"]


# ----------------------------------------------------------------------
# Tests for /reset
# ----------------------------------------------------------------------
def test_reset_db(client, db_session, seed_invoice, seed_transaction):
    """Test that /reset deletes all invoices, transactions, and matches."""
    # Ensure some data exists
    assert db_session.query(Invoice).count() > 0
    assert db_session.query(Transaction).count() > 0

    response = client.delete("/reset")
    assert response.status_code == 200
    assert "Database reset successfully" in response.json()["message"]

    assert db_session.query(Invoice).count() == 0
    assert db_session.query(Transaction).count() == 0
    assert db_session.query(Match).count() == 0


# ----------------------------------------------------------------------
# Tests for /matches
# ----------------------------------------------------------------------
def test_get_all_matches(client, db_session, seed_invoice, seed_transaction):
    """Test retrieving all matches."""
    # Create match1 – auto‑approved (not pending)
    match1 = Match(
        invoice_id=seed_invoice.id,
        transaction_id=seed_transaction.id,
        confidence_score=0.9,
        agent_decision=AgentDecision.AUTO_MATCH,
        agent_notes="Match 1",
        created_at=datetime.utcnow(),
        human_decision=HumanDecision.APPROVED,
        reviewed_at=datetime.utcnow(),
    )
    # Create match2 – pending review
    match2 = Match(
        invoice_id=seed_invoice.id,
        transaction_id=None,
        confidence_score=0.4,
        agent_decision=AgentDecision.NEEDS_REVIEW,
        agent_notes="Match 2",
        created_at=datetime.utcnow(),
    )
    db_session.add_all([match1, match2])
    db_session.commit()

    # Get all matches
    response = client.get("/matches")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Filter by pending – only match2 should be returned
    response = client.get("/matches?status=pending")
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["agent_decision"] == AgentDecision.NEEDS_REVIEW
    assert data["items"][0]["human_decision"] is None


# ----------------------------------------------------------------------
# Tests for /stats
# ----------------------------------------------------------------------
def test_stats_endpoint(client, db_session, seed_invoice, seed_transaction):
    """Test the statistics endpoint."""
    # Create a few matches with different states
    match1 = Match(
        invoice_id=seed_invoice.id,
        transaction_id=seed_transaction.id,
        confidence_score=0.9,
        agent_decision=AgentDecision.AUTO_MATCH,
        agent_notes="Auto",
        created_at=datetime.utcnow(),
        human_decision=HumanDecision.APPROVED,
        reviewed_at=datetime.utcnow(),
    )
    match2 = Match(
        invoice_id=seed_invoice.id,
        transaction_id=None,
        confidence_score=0.4,
        agent_decision=AgentDecision.NEEDS_REVIEW,
        agent_notes="Pending",
        created_at=datetime.utcnow(),
        human_decision=None,
    )
    db_session.add_all([match1, match2])
    db_session.commit()

    response = client.get("/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total_invoices"] == 1
    assert data["total_matches"] == 2
    assert data["pending"] == 1
    assert data["total_reviewed"] == 1
    # Accuracy: the only reviewed match was approved with agent_decision AUTO_MATCH -> correct
    assert data["accuracy"] == 1.0


# ----------------------------------------------------------------------
# Tests for /create-pending/{invoice_id}
# ----------------------------------------------------------------------
def test_create_pending_match(client, db_session, seed_invoice):
    """Test manual creation of a pending match."""
    response = client.post(f"/create-pending/{seed_invoice.id}")
    assert response.status_code == 200
    data = response.json()
    assert "match_id" in data
    assert data["message"] == f"Pending match created for invoice {seed_invoice.id}"

    match = db_session.query(Match).filter_by(id=data["match_id"]).first()
    assert match is not None
    assert match.invoice_id == seed_invoice.id
    assert match.agent_decision == AgentDecision.NEEDS_REVIEW
    assert match.transaction_id is None
    assert match.confidence_score == 0.45


def test_create_pending_match_invoice_not_found(client):
    """Test creating a pending match for a non-existent invoice."""
    response = client.post("/create-pending/999")
    assert response.status_code == 404
    assert "Invoice not found" in response.json()["detail"]