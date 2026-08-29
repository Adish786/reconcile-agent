"""
Unit tests for the advanced reconciliation service.

These tests cover the `run_advanced` function, mocking the LangGraph
workflow to avoid real LLM calls and database side effects.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.models.db_models import Match
from src.models.schemas import AgentDecision
from src.services.advanced import run_advanced


# ----------------------------------------------------------------------
# Tests for advanced reconciliation
# ----------------------------------------------------------------------
@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_auto_match(mock_build_graph, db_session, seed_invoice, seed_transaction):
    """
    Test that run_advanced correctly handles an AUTO_MATCH decision.
    """
    # Create a mock graph that returns an AUTO_MATCH state
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.AUTO_MATCH,
        "confidence": 0.95,
        "transaction_id": seed_transaction.id,
        "notes": "Exact match found",
        "analysis": "Invoice matches transaction perfectly",
    }
    mock_build_graph.return_value = mock_graph

    result = run_advanced(seed_invoice.id, db_session)

    assert result["status"] == AgentDecision.AUTO_MATCH
    assert result["decision"] == AgentDecision.AUTO_MATCH
    assert result["confidence"] == 0.95
    assert result["match_id"] > 0
    assert result.get("transaction_id") == seed_transaction.id

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.AUTO_MATCH
    assert match.confidence_score == 0.95
    assert match.transaction_id == seed_transaction.id
    assert match.agent_notes == "Exact match found"


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_needs_review(mock_build_graph, db_session, seed_invoice):
    """Test NEEDS_REVIEW decision."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.NEEDS_REVIEW,
        "confidence": 0.55,
        "transaction_id": None,
        "notes": "Uncertain match, needs human review",
        "analysis": "Vendor matches but amount differs",
    }
    mock_build_graph.return_value = mock_graph

    result = run_advanced(seed_invoice.id, db_session)

    assert result["status"] == AgentDecision.NEEDS_REVIEW
    assert result["decision"] == AgentDecision.NEEDS_REVIEW
    assert result["confidence"] == 0.55
    assert result["match_id"] > 0
    assert result.get("transaction_id") is None

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.NEEDS_REVIEW
    assert match.confidence_score == 0.55
    assert match.transaction_id is None


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_no_match(mock_build_graph, db_session, seed_invoice):
    """Test NO_MATCH decision."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.NO_MATCH,
        "confidence": 0.0,
        "transaction_id": None,
        "notes": "No matching transaction found",
        "analysis": "None of the candidates match",
    }
    mock_build_graph.return_value = mock_graph

    result = run_advanced(seed_invoice.id, db_session)

    assert result["status"] == AgentDecision.NO_MATCH
    assert result["decision"] == AgentDecision.NO_MATCH
    assert result["confidence"] == 0.0
    assert result["match_id"] > 0
    assert result.get("transaction_id") is None

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.NO_MATCH
    assert match.confidence_score == 0.0
    assert match.transaction_id is None


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_invoice_not_found(mock_build_graph, db_session):
    """Test error when invoice does not exist."""
    result = run_advanced(999, db_session)

    # Graph should NOT be invoked
    mock_build_graph.assert_not_called()

    assert result["status"] == "ERROR"
    assert "Invoice not found" in result["reason"]
    assert result["decision"] == "NO_MATCH"
    assert result["confidence"] == 0.0
    assert result["match_id"] == 0


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_graph_throws_exception(mock_build_graph, db_session, seed_invoice):
    """Test graceful handling of graph invocation errors."""
    mock_graph = MagicMock()
    mock_graph.invoke.side_effect = Exception("Graph execution failed")
    mock_build_graph.return_value = mock_graph

    result = run_advanced(seed_invoice.id, db_session)

    assert result["status"] == "ERROR"
    assert "Agent execution error" in result["reason"]
    assert result["decision"] == "NO_MATCH"
    assert result["confidence"] == 0.0
    assert result["match_id"] == 0

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is None


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_invalid_decision_fallback(mock_build_graph, db_session, seed_invoice):
    """Test that invalid decision values fall back to NO_MATCH."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": "INVALID_DECISION",  # Not valid
        "confidence": 0.8,
        "transaction_id": None,
        "notes": "Invalid decision",
        "analysis": "Something went wrong",
    }
    mock_build_graph.return_value = mock_graph

    result = run_advanced(seed_invoice.id, db_session)

    # Should fall back to NO_MATCH
    assert result["status"] == AgentDecision.NO_MATCH
    assert result["decision"] == AgentDecision.NO_MATCH
    assert result["confidence"] == 0.8  # Confidence preserved
    assert result["match_id"] > 0

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == AgentDecision.NO_MATCH


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_database_error(mock_build_graph, db_session, seed_invoice):
    """Test handling of database commit errors."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.AUTO_MATCH,
        "confidence": 0.95,
        "transaction_id": None,
        "notes": "Match found",
        "analysis": "Perfect match",
    }
    mock_build_graph.return_value = mock_graph

    # Simulate a database commit failure
    original_commit = db_session.commit
    db_session.commit = MagicMock(side_effect=Exception("Database commit failed"))

    try:
        result = run_advanced(seed_invoice.id, db_session)

        assert result["status"] == "ERROR"
        assert "Database error" in result["reason"]
        assert result["decision"] == "NO_MATCH"
        assert result["confidence"] == 0.0
        assert result["match_id"] == 0

        match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
        assert match is None
    finally:
        db_session.commit = original_commit
        db_session.rollback()


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_auto_match_with_notes(mock_build_graph, db_session, seed_invoice, seed_transaction):
    """Test that notes from the graph are stored correctly."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.AUTO_MATCH,
        "confidence": 0.98,
        "transaction_id": seed_transaction.id,
        "notes": "Automatically matched based on vendor and amount",
        "analysis": "Vendor: Acme Corp, Amount: $100.00",
    }
    mock_build_graph.return_value = mock_graph

    result = run_advanced(seed_invoice.id, db_session)

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.agent_notes == "Automatically matched based on vendor and amount"


@patch("src.services.advanced.build_reconciliation_graph")
def test_advanced_partial_state(mock_build_graph, db_session, seed_invoice):
    """Test handling of a partial state (missing fields)."""
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "decision": AgentDecision.NO_MATCH,
        # confidence, transaction_id, notes, analysis are missing
    }
    mock_build_graph.return_value = mock_graph

    result = run_advanced(seed_invoice.id, db_session)

    # Should use defaults for missing fields
    assert result["status"] == AgentDecision.NO_MATCH
    assert result["decision"] == AgentDecision.NO_MATCH
    assert result["confidence"] == 0.0  # default
    assert result["match_id"] > 0

    match = db_session.query(Match).filter_by(invoice_id=seed_invoice.id).first()
    assert match is not None
    assert match.confidence_score == 0.0
    assert match.transaction_id is None
    assert match.agent_notes == ""  # default   
    