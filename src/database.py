"""
Database configuration and session management for the Reconcile Agent.

This module sets up:
- SQLAlchemy engine with connection pooling.
- SessionLocal factory for creating database sessions.
- Base class for declarative models.
- Dependency function `get_db` for FastAPI endpoints.
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from src.config import settings

# ----------------------------------------------------------------------
# Engine configuration
# ----------------------------------------------------------------------
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,          # Verify connections before using them
    pool_size=5,                 # Number of connections to keep in the pool
    max_overflow=10,             # Extra connections when pool is exhausted
    pool_recycle=3600,           # Recycle connections after 1 hour
    echo=settings.LOG_LEVEL == "DEBUG",  # Log SQL queries in debug mode
)

# ----------------------------------------------------------------------
# Session factory
# ----------------------------------------------------------------------
SessionLocal = sessionmaker(
    autocommit=False,           # Explicit commits required
    autoflush=False,            # Flush only on explicit commit
    bind=engine,
)

# ----------------------------------------------------------------------
# Base class for ORM models
# ----------------------------------------------------------------------
Base = declarative_base()


# ----------------------------------------------------------------------
# Dependency for FastAPI: get database session
# ----------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """
    Provide a database session for a FastAPI request.

    This function is used as a dependency in route handlers.
    It yields a session that is automatically closed after the request.

    Yields:
        Session: SQLAlchemy session.

    Example:
        @app.get("/items")
        def read_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------------------------------------------------------------
# Optional: health check function for database connectivity
# ----------------------------------------------------------------------
def check_database_health() -> bool:
    """
    Verify that the database is reachable and responsive.

    Returns:
        bool: True if the database connection is healthy, False otherwise.
    """
    try:
        with SessionLocal() as session:
            session.execute("SELECT 1")
        return True
    except Exception:
        return False