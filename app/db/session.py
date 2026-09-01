"""Database session management."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a scoped SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
