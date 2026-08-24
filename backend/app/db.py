"""
SQLAlchemy engine + session setup, and the FastAPI dependency for getting a DB session
per-request.

No migration framework (Alembic) yet — `Base.metadata.create_all()` at startup is a
deliberate simplification for this stage of the project, not an oversight. It works fine
while the schema is still small and changing quickly, but doesn't handle schema *changes*
to existing data (it only creates tables that don't exist yet, never alters existing ones).
Alembic is the right tool once the schema stabilizes and there's real data to migrate.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

engine = create_engine(settings.postgres_dsn)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Session:
    """FastAPI dependency — yields a session, always closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
