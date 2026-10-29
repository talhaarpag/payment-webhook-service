import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.signature import build_header

SECRET = "whsec_test_secret"


@pytest.fixture
def settings(tmp_path):
    return Settings(webhook_secret=SECRET, db_path=str(tmp_path / "test.db"), retry_interval=0)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def make_event(event_id="evt_1", type_="payment_intent.succeeded", created=None, **obj):
    data = {"id": "pi_1", "amount": 2000, "currency": "usd", "receipt_email": "buyer@example.com"}
    data.update(obj)
    return {"id": event_id, "type": type_, "created": created or int(time.time()), "data": {"object": data}}


def post_event(client, event, secret=SECRET, header=None):
    body = json.dumps(event).encode()
    return client.post(
        "/webhooks/stripe",
        content=body,
        headers={"Stripe-Signature": header or build_header(body, secret), "Content-Type": "application/json"},
    )
