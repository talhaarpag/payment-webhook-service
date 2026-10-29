import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,          -- Stripe event id: the idempotency key
    type          TEXT NOT NULL,
    payload       TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('received', 'processed', 'ignored', 'failed', 'dead')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    next_retry_at REAL,
    received_at   TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_retry ON events (status, next_retry_at);

CREATE TABLE IF NOT EXISTS payments (
    id                 TEXT PRIMARY KEY,     -- PaymentIntent id
    amount             INTEGER NOT NULL,     -- smallest currency unit, as Stripe sends it
    amount_refunded    INTEGER NOT NULL DEFAULT 0,
    currency           TEXT NOT NULL,
    status             TEXT NOT NULL,
    customer_email     TEXT,
    last_event_created INTEGER NOT NULL,     -- guards against out-of-order delivery
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def connect(db_path: str):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
