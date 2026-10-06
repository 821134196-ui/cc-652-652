"""SQLite engine / session management.

WAL + a generous busy timeout are important: concurrent signatures are
serialized by SQLite's write lock instead of failing with "database is locked".
Correctness under concurrency is still enforced by UNIQUE constraints, not by
the lock timeout (see models.ConsentVersion / Signature).
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("APP_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.environ.get("APP_DB_PATH", DATA_DIR / "consent.db"))
CONSENT_FILE_DIR = DATA_DIR / "consents"
SIGN_GATEWAY_LOG = DATA_DIR / "sign_gateway.log"
NOTIFY_GATEWAY_LOG = DATA_DIR / "notify_gateway.log"
CONSENT_FILE_DIR.mkdir(parents=True, exist_ok=True)

CONNECT_URL = os.environ.get("APP_DB_URL", f"sqlite:///{DB_PATH}")


class Base(DeclarativeBase):
    pass


engine = create_engine(
    CONNECT_URL,
    connect_args={"check_same_thread": False, "timeout": 15},
    future=True,
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
    import sqlite3

    if isinstance(dbapi_connection, sqlite3.Connection):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(bind=engine)
