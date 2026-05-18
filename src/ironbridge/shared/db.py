import os
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from ironbridge.shared.framework.resource import Base  # noqa: F401 — re-exported for Alembic

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://app:app@localhost:5432/ironbridge",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def tenant_session(tenant_id: str) -> Generator[Session, None, None]:
    """
    Yield a Session with app.tenant_id set for the connection lifetime.
    Postgres RLS policies enforce tenant isolation automatically.
    """
    db = SessionLocal()
    try:
        db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
