import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.database import Base, get_db
from src.main import app
from fastapi.testclient import TestClient
from src.models.db_models import Invoice, Transaction
from datetime import datetime, timedelta

# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture(scope="function")
def db_session():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.rollback()
    session.close()
    Base.metadata.drop_all(bind=engine)

@pytest.fixture
def client():
    return TestClient(app)

@pytest.fixture
def seed_invoice(db_session):
    inv = Invoice(
        vendor="Acme Corp",
        invoice_number="INV-1001",
        amount=100.0,
        currency="USD",
        due_date=datetime.now() + timedelta(days=5)
    )
    db_session.add(inv)
    db_session.commit()
    return inv

@pytest.fixture
def seed_transaction(db_session):
    tx = Transaction(
        date=datetime.now(),
        description="ACME CORP PAYMENT",
        amount=100.0,
        currency="USD"
    )
    db_session.add(tx)
    db_session.commit()
    return tx