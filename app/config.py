import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    webhook_secret: str
    db_path: str = "webhooks.db"
    signature_tolerance: int = 300  # seconds; Stripe's default replay window
    max_attempts: int = 5
    retry_base_seconds: int = 30  # backoff: 30s, 60s, 120s, ...
    retry_interval: int = 15  # how often the background worker looks for due retries; 0 disables it
    admin_api_key: str | None = None


def load_settings() -> Settings:
    secret = os.getenv("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not set (see .env.example)")
    return Settings(
        webhook_secret=secret,
        db_path=os.getenv("DB_PATH", "webhooks.db"),
        signature_tolerance=int(os.getenv("SIGNATURE_TOLERANCE", "300")),
        max_attempts=int(os.getenv("MAX_ATTEMPTS", "5")),
        retry_base_seconds=int(os.getenv("RETRY_BASE_SECONDS", "30")),
        retry_interval=int(os.getenv("RETRY_INTERVAL", "15")),
        admin_api_key=os.getenv("ADMIN_API_KEY") or None,
    )
