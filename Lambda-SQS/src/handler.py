# Copyright 2025 Loopper-AI
# Lambda handler: SQS → POST to app-server /queue/webhook
#
# SQS event source mapping invokes with {"Records": [...]}.
# POST full event to app server. App returns 202 and processes in background.
#
# Uses ReportBatchItemFailures:
#   2xx  → no batchItemFailures → messages deleted from queue
#   fail → batchItemFailures for all records → SQS retries → DLQ

from __future__ import annotations

import json
import logging
from typing import Any

from .clients import HttpClient
from .config import Config

logger = logging.getLogger()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """SQS event source mapping → POST to app server."""
    config = Config.from_environment()
    logger.setLevel(getattr(logging, config.log_level, logging.INFO))

    request_id = getattr(context, "aws_request_id", "") if context else ""
    logger.info("lambda_handler started request_id=%s", request_id)

    is_valid, err = config.validate()
    if not is_valid:
        logger.error("Configuration error: %s", err)
        raise ValueError(err)

    # Extract message IDs for batch failure reporting
    records = event.get("Records") or []
    message_ids = [r["messageId"] for r in records if isinstance(r, dict) and "messageId" in r]
    logger.info("Processing: record_count=%d message_ids=%s", len(message_ids), message_ids)

    # Forward full SQS event to app server
    payload = json.dumps(event, default=str).encode("utf-8")
    result = HttpClient(timeout=config.request_timeout).post_json(config.webhook_url, payload)

    if result.success:
        logger.info("Forward success: status=%s request_id=%s", result.status_code, request_id)
        # Parse app response or return empty dict
        if result.response_body.strip():
            try:
                return json.loads(result.response_body)
            except json.JSONDecodeError:
                return {"status": result.status_code, "body": result.response_body[:200]}
        return {}

    # Failure — report all records as failed for retry → DLQ
    logger.warning("Forward failed: error=%s status=%s request_id=%s", result.error, result.status_code, request_id)
    failures = [{"itemIdentifier": mid} for mid in message_ids]
    if failures:
        logger.warning("Returning batchItemFailures: count=%d", len(failures))
        return {"batchItemFailures": failures}
    return {}
