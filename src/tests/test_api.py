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
    # After approval, the invoice should be marked as PAID, not PENDING
    # assert invoice.status == InvoiceStatus.PENDING.value

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