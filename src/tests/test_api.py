def test_reconcile_endpoint(client, db_session, seed_invoice, seed_transaction):
    response = client.post(f"/reconcile/{seed_invoice.id}?use_advanced=true")
    assert response.status_code == 200
    data = response.json()
    assert data["invoice_id"] == seed_invoice.id
    assert data["decision"] in ("AUTO_MATCH", "NEEDS_REVIEW", "NO_MATCH")

def test_review_queue(client, db_session, seed_invoice):
    # Create a match needing review
    from src.models.db_models import Match
    match = Match(
        invoice_id=seed_invoice.id,
        transaction_id=None,
        confidence_score=0.5,
        agent_decision="NEEDS_REVIEW",
        agent_notes="Test review"
    )
    db_session.add(match)
    db_session.commit()
    response = client.get("/review/queue")
    assert response.status_code == 200
    assert response.json()["pending_count"] >= 1

def test_review_update(client, db_session, seed_invoice):
    from src.models.db_models import Match
    match = Match(
        invoice_id=seed_invoice.id,
        transaction_id=None,
        confidence_score=0.5,
        agent_decision="NEEDS_REVIEW",
        agent_notes="Test"
    )
    db_session.add(match)
    db_session.commit()
    response = client.put(f"/review/{match.id}", json={"decision": "APPROVED", "notes": "Looks good"})
    assert response.status_code == 200
    db_session.refresh(match)
    assert match.human_decision == "APPROVED"
    assert match.agent_decision == "NEEDS_REVIEW"