"""Send a correctly signed fake Stripe event to a running instance.

    python scripts/send_test_event.py succeeded
    python scripts/send_test_event.py refunded --amount-refunded 500
    python scripts/send_test_event.py succeeded --event-id evt_same   # run twice to see idempotency
    python scripts/send_test_event.py succeeded --bad-signature
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.signature import build_header  # noqa: E402


def build_event(kind: str, event_id: str, pi_id: str, amount: int, refunded: int) -> dict:
    types = {
        "succeeded": "payment_intent.succeeded",
        "failed": "payment_intent.payment_failed",
        "refunded": "charge.refunded",
    }
    obj = {"id": pi_id, "amount": amount, "currency": "usd", "receipt_email": "buyer@example.com"}
    if kind == "refunded":
        obj = {**obj, "id": "ch_" + pi_id[3:], "payment_intent": pi_id, "amount_refunded": refunded}
    return {"id": event_id, "type": types[kind], "created": int(time.time()), "data": {"object": obj}}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["succeeded", "failed", "refunded"])
    p.add_argument("--url", default="http://127.0.0.1:8000/webhooks/stripe")
    p.add_argument("--event-id", default=f"evt_{uuid.uuid4().hex[:12]}")
    p.add_argument("--payment-id", default="pi_demo_1")
    p.add_argument("--amount", type=int, default=4999)
    p.add_argument("--amount-refunded", type=int, default=4999)
    p.add_argument("--bad-signature", action="store_true")
    args = p.parse_args()

    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_local_dev_secret")
    event = build_event(args.kind, args.event_id, args.payment_id, args.amount, args.amount_refunded)
    body = json.dumps(event).encode()
    header = build_header(body, "wrong" if args.bad_signature else secret)

    req = urllib.request.Request(
        args.url, data=body, method="POST", headers={"Content-Type": "application/json", "Stripe-Signature": header}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(resp.status, resp.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())


if __name__ == "__main__":
    main()
