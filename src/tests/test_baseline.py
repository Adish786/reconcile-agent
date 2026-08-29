import pytest
from src.services.baseline import run_baseline
from src.models.db_models import Match

def test_baseline_match_found(db_session, seed_invoice, seed_transaction):
    result = run_baseline(seed_invoice.id, db_session)
    assert result["status"] == "AUTO_MATCHED"
    # Check match was created
    match = db_session.query(Match).filter(Match.invoice_id == seed_invoice.id).first()
    assert match is not None
    assert match.transaction_id == seed_transaction.id

def test_baseline_no_match(db_session, seed_invoice):
    # No transaction seeded
    result = run_baseline(seed_invoice.id, db_session)
    assert result["status"] == "UNMATCHED"
    # Check no match was created
    match = db_session.query(Match).filter(Match.invoice_id == seed_invoice.id).first()
    assert match is None