"""Event storage, business handlers and the retry loop."""

import json
import logging
import time

from .config import Settings
from .db import connect

log = logging.getLogger("webhooks")


def _upsert_payment(conn, pi: dict, created: int, status: str, refunded: int | None = None) -> None:
    # The WHERE clause drops stale events: Stripe does not guarantee delivery order.
    conn.execute(
        """
        INSERT INTO payments (id, amount, currency, status, customer_email, last_event_created, amount_refunded)
        VALUES (:id, :amount, :currency, :status, :email, :created, COALESCE(:refunded, 0))
        ON CONFLICT (id) DO UPDATE SET
            amount = excluded.amount,
            currency = excluded.currency,
            status = excluded.status,
            customer_email = COALESCE(excluded.customer_email, payments.customer_email),
            amount_refunded = COALESCE(:refunded, payments.amount_refunded),
            last_event_created = excluded.last_event_created,
            updated_at = datetime('now')
        WHERE excluded.last_event_created >= payments.last_event_created
        """,
        {
            "id": pi["id"],
            "amount": pi["amount"],
            "currency": pi["currency"],
            "status": status,
            "email": pi.get("receipt_email"),
            "created": created,
            "refunded": refunded,
        },
    )


def handle_payment_intent_succeeded(conn, event: dict) -> None:
    _upsert_payment(conn, event["data"]["object"], event["created"], "succeeded")


def handle_payment_intent_failed(conn, event: dict) -> None:
    _upsert_payment(conn, event["data"]["object"], event["created"], "failed")


def handle_charge_refunded(conn, event: dict) -> None:
    charge = event["data"]["object"]
    pi = {
        "id": charge["payment_intent"],
        "amount": charge["amount"],
        "currency": charge["currency"],
        "receipt_email": charge.get("receipt_email"),
    }
    status = "refunded" if charge["amount_refunded"] >= charge["amount"] else "partially_refunded"
    _upsert_payment(conn, pi, event["created"], status, refunded=charge["amount_refunded"])


HANDLERS = {
    "payment_intent.succeeded": handle_payment_intent_succeeded,
    "payment_intent.payment_failed": handle_payment_intent_failed,
    "charge.refunded": handle_charge_refunded,
}


def store_event(settings: Settings, event: dict) -> bool:
    """Persist the raw event. Returns False if this event id was already seen."""
    with connect(settings.db_path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO events (id, type, payload, status) VALUES (?, ?, ?, 'received')",
            (event["id"], event["type"], json.dumps(event)),
        )
        return cur.rowcount == 1


def process_event(settings: Settings, event_id: str, now: float | None = None) -> str:
    """Run the handler for one stored event and record the outcome. Returns the new status."""
    now = time.time() if now is None else now
    with connect(settings.db_path) as conn:
        row = conn.execute("SELECT type, payload, attempts FROM events WHERE id = ?", (event_id,)).fetchone()
        handler = HANDLERS.get(row["type"])
        if handler is None:
            conn.execute(
                "UPDATE events SET status = 'ignored', processed_at = datetime('now') WHERE id = ?", (event_id,)
            )
            return "ignored"

        attempts = row["attempts"] + 1
        try:
            handler(conn, json.loads(row["payload"]))
        except Exception as exc:  # any handler bug or bad payload becomes a retryable failure
            conn.rollback()
            if attempts >= settings.max_attempts:
                status, next_retry = "dead", None
            else:
                status, next_retry = "failed", now + settings.retry_base_seconds * 2 ** (attempts - 1)
            conn.execute(
                "UPDATE events SET status = ?, attempts = ?, last_error = ?, next_retry_at = ? WHERE id = ?",
                (status, attempts, f"{type(exc).__name__}: {exc}", next_retry, event_id),
            )
            log.warning("event %s failed (attempt %d, %s): %s", event_id, attempts, status, exc)
            return status

        conn.execute(
            """UPDATE events SET status = 'processed', attempts = ?, last_error = NULL,
               next_retry_at = NULL, processed_at = datetime('now') WHERE id = ?""",
            (attempts, event_id),
        )
        return "processed"


def retry_due_events(settings: Settings, now: float | None = None) -> dict[str, str]:
    now = time.time() if now is None else now
    with connect(settings.db_path) as conn:
        due = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM events WHERE status = 'failed' AND next_retry_at <= ? ORDER BY next_retry_at",
                (now,),
            )
        ]
    return {event_id: process_event(settings, event_id, now) for event_id in due}
