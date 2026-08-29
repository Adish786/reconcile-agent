import pytest
from unittest.mock import patch, MagicMock
from src.services.advanced import run_advanced
from src.models.db_models import Match

@patch("src.agents.nodes.llm.invoke")
def test_advanced_auto_match(mock_llm, db_session, seed_invoice, seed_transaction):
    # Mock LLM to return a high-confidence match
    mock_llm.return_value.content = '{"analysis":"Match is clear","decision":"AUTO_MATCH","confidence":0.95,"transaction_id":' + str(seed_transaction.id) + '}'
    result = run_advanced(seed_invoice.id, db_session)
    assert result["decision"] == "AUTO_MATCH"
    assert result["confidence"] >= 0.9
    # Check match
    match = db_session.query(Match).filter(Match.invoice_id == seed_invoice.id).first()
    assert match is not None
    assert match.agent_decision == "AUTO_MATCH"

@patch("src.agents.nodes.llm.invoke")
def test_advanced_needs_review(mock_llm, db_session, seed_invoice):
    # No transaction seeded, but LLM might hallucinate; we want fallback or no match
    mock_llm.return_value.content = '{"analysis":"Uncertain","decision":"NEEDS_REVIEW","confidence":0.4,"transaction_id":null}'
    result = run_advanced(seed_invoice.id, db_session)
    assert result["decision"] == "NEEDS_REVIEW" or result["decision"] == "NO_MATCH"
    match = db_session.query(Match).filter(Match.invoice_id == seed_invoice.id).first()
    assert match is not None
    # If no match, confidence low
    if match.agent_decision == "NO_MATCH":
        assert result["confidence"] < 0.5
    elif match.agent_decision == "NEEDS_REVIEW":
        assert result["confidence"] < 0.7