"""
Integration tests for the Reconcile Agent API.

This module tests all endpoints with a test database and mocked external dependencies.
The tests cover:
- Invoice upload
- Baseline reconciliation (rule-based)
- Advanced reconciliation (LLM-based, mocked)
- Review queue and review updates
- Error handling and edge cases
"""

import csv
import io
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database import Base, get_db
from src.main import app
from src.models.db_models import Invoice, Match, Transaction
from src.models.schemas import AgentDecision, HumanDecision, InvoiceStatus

# ----------------------------------------------------------------------
# Test configuration
# ----------------------------------------------------------------------
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Override the database dependency for testing."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="function")
def db_session():
    """Provide a clean database session for each test."""
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.rollback()
    session.close()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
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


@pytest.fixture
def seed_match(db_session, seed_invoice, seed_transaction):
    """Create a test match record."""
    match = Match(
        invoice_id=seed_invoice.id,
        transaction_id=seed_transaction.id,
        confidence_score=0.95,
        agent_decision=AgentDecision.AUTO_MATCH,
        agent_notes="Test match",
        created_at=datetime.utcnow(),
    )
    db_session.add(match)
    db_session.commit()
    db_session.refresh(match)
    return match


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def create_csv_content(rows):
    """Create CSV content as bytes from a list of dict rows."""
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
            "invoice_number": "INV-002",
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
    # Verify database
    invoice = db_session.query(Invoice).filter_by(invoice_number="INV-002").first()
    assert invoice is not None
    assert invoice.vendor == "Test Corp"


def test_upload_invoices_missing_columns(client):
    """Test CSV with missing required columns."""
    csv_data = b"vendor,amount\nTest Corp,100.00\n"  # missing invoice_number, due_date
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
    assert "due_date" in response.json()["detail"].lower()


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
    # Verify Match record
    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.AUTO_MATCH
    assert match.transaction_id == seed_transaction.id


def test_baseline_no_match(client, db_session, seed_invoice):
    """Test baseline reconciliation when no transaction matches."""
    response = client.post(f"/reconcile/{seed_invoice.id}?use_advanced=false")
    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == seed_invoice.id
    assert data["decision"] == "NO_MATCH"
    assert data["confidence"] == 0.0
    # Verify Match record
    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
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
@patch("src.agents.nodes.llm")
def test_advanced_match(mock_llm, client, db_session, seed_invoice, seed_transaction):
    """Test advanced reconciliation with a mocked LLM response."""
    # Mock LLM response
    mock_response = Mock()
    mock_response.content = json.dumps({
        "analysis": "Mock match found",
        "decision": AgentDecision.AUTO_MATCH,
        "confidence": 0.95,
        "transaction_id": seed_transaction.id,
    })
    mock_llm.invoke.return_value = mock_response

    response = client.post(f"/reconcile/{seed_invoice.id}?use_advanced=true")
    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == seed_invoice.id
    assert data["decision"] == AgentDecision.AUTO_MATCH
    assert data["confidence"] == 0.95
    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.AUTO_MATCH


@patch("src.agents.nodes.llm")
def test_advanced_no_candidates(mock_llm, client, db_session, seed_invoice):
    """Test advanced when there are no candidate transactions."""
    # No transactions exist, so candidates list will be empty.
    response = client.post(f"/reconcile/{seed_invoice.id}?use_advanced=true")
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] == AgentDecision.NO_MATCH
    assert data["confidence"] == 0.0


# ----------------------------------------------------------------------
# Tests for /review/queue
# ----------------------------------------------------------------------
def test_review_queue_empty(client):
    """Test review queue when there are no pending reviews."""
    response = client.get("/review/queue")
    assert response.status_code == 200
    data = response.json()
    assert data["pending_count"] == 0
    assert data["items"] == []


def test_review_queue_with_pending(client, db_session, seed_invoice, seed_transaction):
    """Test review queue with a pending match."""
    # Create a match with NEEDS_REVIEW
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
def test_review_update_approve(client, db_session, seed_match):
    """Test approving a match."""
    payload = {"decision": "APPROVED", "notes": "Looks good"}
    response = client.put(f"/review/{seed_match.id}", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "updated", "match_id": seed_match.id}
    # Verify database
    db_session.refresh(seed_match)
    assert seed_match.human_decision == HumanDecision.APPROVED
    assert seed_match.human_notes == "Looks good"
    assert seed_match.reviewed_at is not None
    # Verify invoice status updated
    invoice = db_session.query(Invoice).filter_by(id=seed_match.invoice_id).first()
    assert invoice.status == InvoiceStatus.PAID


def test_review_update_reject(client, db_session, seed_match):
    """Test rejecting a match."""
    payload = {"decision": "REJECTED", "notes": "Not a match"}
    response = client.put(f"/review/{seed_match.id}", json=payload)
    assert response.status_code == 200
    db_session.refresh(seed_match)
    assert seed_match.human_decision == HumanDecision.REJECTED
    # Invoice status should remain PENDING
    invoice = db_session.query(Invoice).filter_by(id=seed_match.invoice_id).first()
    assert invoice.status == InvoiceStatus.PENDING


def test_review_update_invalid_decision(client, db_session, seed_match):
    """Test review update with invalid decision."""
    payload = {"decision": "INVALID", "notes": "Test"}
    response = client.put(f"/review/{seed_match.id}", json=payload)
    assert response.status_code == 400
    assert "decision must be APPROVED or REJECTED" in response.json()["detail"]


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