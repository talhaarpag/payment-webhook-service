import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from . import processor, signature
from .config import Settings, load_settings
from .db import connect, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("webhooks")


async def _retry_worker(settings: Settings) -> None:
    while True:
        await asyncio.sleep(settings.retry_interval)
        try:
            results = await asyncio.to_thread(processor.retry_due_events, settings)
            if results:
                log.info("retried %d event(s): %s", len(results), results)
        except Exception:
            log.exception("retry worker error")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(settings.db_path)
        task = asyncio.create_task(_retry_worker(settings)) if settings.retry_interval > 0 else None
        yield
        if task:
            task.cancel()

    app = FastAPI(
        title="Payment Webhook Service",
        description="Receives Stripe webhooks with signature verification, idempotency and automatic retries.",
        version="1.0.0",
        lifespan=lifespan,
    )

    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if settings.admin_api_key and x_admin_key != settings.admin_api_key:
            raise HTTPException(status_code=401, detail="invalid admin key")

    @app.get("/health")
    def health():
        with connect(settings.db_path) as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request, stripe_signature: str | None = Header(default=None)):
        payload = await request.body()  # raw bytes: re-serialized JSON would break the signature
        try:
            signature.verify(payload, stripe_signature, settings.webhook_secret, settings.signature_tolerance)
        except signature.SignatureError as exc:
            log.warning("rejected webhook: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            event = json.loads(payload)
            event_id, event_type = event["id"], event["type"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status_code=400, detail="invalid event payload")

        if not await asyncio.to_thread(processor.store_event, settings, event):
            return {"received": True, "duplicate": True}

        # Always 2xx once the event is stored: failures are retried by us, not by Stripe.
        status = await asyncio.to_thread(processor.process_event, settings, event_id)
        log.info("event %s (%s) -> %s", event_id, event_type, status)
        return {"received": True, "status": status}

    @app.get("/payments", dependencies=[Depends(require_admin)])
    def list_payments(limit: int = 50):
        with connect(settings.db_path) as conn:
            rows = conn.execute("SELECT * FROM payments ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    @app.get("/events", dependencies=[Depends(require_admin)])
    def list_events(status: str | None = None, limit: int = 50):
        query = "SELECT id, type, status, attempts, last_error, next_retry_at, received_at, processed_at FROM events"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        with connect(settings.db_path) as conn:
            rows = conn.execute(query + " ORDER BY received_at DESC LIMIT ?", (*params, limit)).fetchall()
        return [dict(r) for r in rows]

    @app.post("/events/retry", dependencies=[Depends(require_admin)])
    def retry_now():
        return processor.retry_due_events(settings)

    return app
