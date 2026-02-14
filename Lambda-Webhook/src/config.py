# Copyright 2025 Loopper-AI
# Configuration management for Lambda-Webhook

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable application configuration from environment variables."""

    queue_url: str
    secrets_arn: str | None = None
    process_created: bool = True
    process_updated: bool = True
    api_timeout: float = 30.0
    log_level: str = "INFO"

    @classmethod
    def from_environment(cls) -> Config:
        queue_url = os.environ.get("QUEUE_URL", "")
        secrets_arn = os.environ.get("SECRETS_ARN")
        process_created = os.environ.get("PROCESS_CREATED", "true").lower() in ("1", "true", "yes")
        process_updated = os.environ.get("PROCESS_UPDATED", "true").lower() in ("1", "true", "yes")
        api_timeout = float(os.environ.get("API_TIMEOUT", "30.0"))
        log_level = os.environ.get("LOG_LEVEL", "INFO")

        return cls(
            queue_url=queue_url,
            secrets_arn=secrets_arn,
            process_created=process_created,
            process_updated=process_updated,
            api_timeout=api_timeout,
            log_level=log_level,
        )

    def validate(self) -> tuple[bool, str | None]:
        if not self.queue_url:
            return False, "QUEUE_URL not configured"
        return True, None

    def should_process_event(self, event_type: str) -> bool:
        if event_type == "created":
            return self.process_created
        if event_type == "updated":
            return self.process_updated
        return True
