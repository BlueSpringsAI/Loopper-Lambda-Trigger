# Copyright 2025 Loopper-AI
# HTTP response utilities for API Gateway

from __future__ import annotations

import json
from typing import Any


def create_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """
    Create a standardized API Gateway response.

    Args:
        status_code: HTTP status code
        body: Response body as dictionary

    Returns:
        API Gateway response dictionary
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, default=str),
    }
