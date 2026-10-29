import json

from app import processor
from app.signature import build_header

from .conftest import make_event, post_event


def payments(client):
    return {p["id"]: p for p in client.get("/payments").json()}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_bad_signature_is_rejected_and_nothing_stored(client):
    resp = post_event(client, make_event(), secret="whsec_wrong")
    assert resp.status_code == 400
    assert client.get("/events").json() == []


def test_succeeded_payment_is_recorded(client):
    resp = post_event(client, make_event())
    assert resp.json() == {"received": True, "status": "processed"}
    p = payments(client)["pi_1"]
    assert (p["status"], p["amount"], p["customer_email"]) == ("succeeded", 2000, "buyer@example.com")


def test_duplicate_delivery_is_processed_once(client):
    event = make_event()
    post_event(client, event)
    resp = post_event(client, event)
    assert resp.json() == {"received": True, "duplicate": True}
    assert len(client.get("/events").json()) == 1


def test_unknown_event_type_is_ignored(client):
    resp = post_event(client, make_event(type_="customer.created"))
    assert resp.json()["status"] == "ignored"
    assert payments(client) == {}


def test_refund_updates_payment(client):
    post_event(client, make_event(created=100))
    refund = make_event(
        "evt_2", "charge.refunded", created=200, id="ch_1", payment_intent="pi_1", amount_refunded=500
    )
    post_event(client, refund)
    p = payments(client)["pi_1"]
    assert (p["status"], p["amount_refunded"]) == ("partially_refunded", 500)


def test_out_of_order_event_does_not_overwrite_newer_state(client):
    post_event(client, make_event("evt_new", created=200))
    post_event(client, make_event("evt_old", "payment_intent.payment_failed", created=100))
    assert payments(client)["pi_1"]["status"] == "succeeded"


def test_invalid_json_with_valid_signature_returns_400(client, settings):
    body = b"not json"
    resp = client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": build_header(body, settings.webhook_secret)})
    assert resp.status_code == 400


def test_failed_event_is_retried_with_backoff_then_succeeds(client, settings, monkeypatch):
    real = processor.HANDLERS["payment_intent.succeeded"]
    calls = {"n": 0}

    def flaky(conn, event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("downstream timeout")
        real(conn, event)

    monkeypatch.setitem(processor.HANDLERS, "payment_intent.succeeded", flaky)

    resp = post_event(client, make_event())
    assert resp.status_code == 200 and resp.json()["status"] == "failed"
    ev = client.get("/events", params={"status": "failed"}).json()[0]
    assert ev["attempts"] == 1 and "downstream timeout" in ev["last_error"]

    # Not due yet: nothing happens.
    assert processor.retry_due_events(settings, now=ev["next_retry_at"] - 1) == {}
    # Due: retried and processed.
    assert processor.retry_due_events(settings, now=ev["next_retry_at"]) == {"evt_1": "processed"}
    assert payments(client)["pi_1"]["status"] == "succeeded"


def test_event_goes_dead_after_max_attempts(client, settings, monkeypatch):
    def broken(conn, event):
        raise ValueError("bad data")

    monkeypatch.setitem(processor.HANDLERS, "payment_intent.succeeded", broken)
    post_event(client, make_event())
    for day in range(1, settings.max_attempts + 1):  # advance the clock past each backoff
        processor.retry_due_events(settings, now=2_000_000_000 + day * 86_400)

    ev = client.get("/events").json()[0]
    assert ev["status"] == "dead" and ev["attempts"] == settings.max_attempts


def test_failed_handler_leaves_no_partial_writes(client, settings, monkeypatch):
    def half_done(conn, event):
        conn.execute(
            "INSERT INTO payments (id, amount, currency, status, last_event_created) VALUES ('pi_x', 1, 'usd', 'x', 0)"
        )
        raise RuntimeError("crash after write")

    monkeypatch.setitem(processor.HANDLERS, "payment_intent.succeeded", half_done)
    post_event(client, make_event())
    assert payments(client) == {}


def test_admin_key_protects_read_endpoints(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    s = Settings(webhook_secret="s", db_path=str(tmp_path / "a.db"), retry_interval=0, admin_api_key="k")
    with TestClient(create_app(s)) as c:
        assert c.get("/payments").status_code == 401
        assert c.get("/payments", headers={"X-Admin-Key": "k"}).status_code == 200
        assert c.get("/health").status_code == 200
