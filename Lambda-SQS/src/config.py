# Copyright 2025 Loopper-AI
# Configuration management for Lambda-SQS

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_WEBHOOK_PATH = "/queue/webhook"


@dataclass(frozen=True)
class Config:
    """Immutable configuration from environment variables."""

    webhook_url: str
    request_timeout: int
    log_level: str

    @classmethod
    def from_environment(cls) -> Config:
        """Load config. Resolves webhook URL from APP_WEBHOOK_URL or APP_SERVER_URL."""
        webhook_url = (os.environ.get("APP_WEBHOOK_URL") or "").strip().rstrip("/")
        if not webhook_url:
            base = (os.environ.get("APP_SERVER_URL") or "").strip().rstrip("/")
            if base:
                webhook_url = f"{base}{DEFAULT_WEBHOOK_PATH}"

        return cls(
            webhook_url=webhook_url,
            request_timeout=int(os.environ.get("REQUEST_TIMEOUT", "15")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        )

    def validate(self) -> tuple[bool, str | None]:
        if not self.webhook_url:
            return False, "APP_WEBHOOK_URL or APP_SERVER_URL required"
        return True, None
