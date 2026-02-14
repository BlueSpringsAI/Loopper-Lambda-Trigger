# Copyright 2025 Loopper-AI
# Data models for Lambda-SQS

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ForwardResult:
    """Result of POSTing to the app server."""

    success: bool
    status_code: int | None = None
    response_body: str = ""
    error: str | None = None
