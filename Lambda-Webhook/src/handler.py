# Copyright 2025 Loopper-AI
# Lambda handler: Freshdesk webhook → parse & format → SQS FIFO
#
# Return 200 after queueing to prevent Freshdesk retries.
# Raw fallback ensures no tickets are lost.

from __future__ import annotations

import base64
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from .clients import FreshdeskClient, SecretsClient
from .config import Config
from .models import AgentInput
from .parsers import TicketParser, WebhookParser
from .services import SQSService
from .utils import create_response

logger = logging.getLogger()


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway POST → parse Freshdesk webhook → SQS FIFO."""
    config = Config.from_environment()
    logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

    is_valid, error_msg = config.validate()
    if not is_valid:
        logger.error("Configuration error: %s", error_msg)
        return create_response(500, {"status": "error", "message": error_msg})

    # Parse API Gateway body
    raw_body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8", errors="replace")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return create_response(400, {"status": "rejected", "message": "Invalid JSON"})

    if not isinstance(payload, dict):
        return create_response(400, {"status": "rejected", "message": "Body must be JSON object"})

    # Extract event info
    event_type = WebhookParser.get_event_type(payload)
    ticket_id = WebhookParser.extract_ticket_id(payload)
    logger.info("event_type=%s ticket_id=%s", event_type, ticket_id)

    # Check if event should be processed
    if not config.should_process_event(event_type):
        return create_response(200, {"status": "skipped", "ticket_id": ticket_id, "reason": f"{event_type} not processed"})

    # Resolve agent input
    agent_input, parse_error = _resolve_agent_input(payload, event_type, ticket_id, config)

    # Send to SQS
    sqs = SQSService(config.queue_url)
    request_id = event.get("requestContext", {}).get("requestId", "")

    if agent_input is not None:
        dedup_id = _dedup_id(raw_body, request_id)
        msg_id = sqs.send_message(agent_input.to_dict(), agent_input.ticket_id, dedup_id)
        if msg_id:
            return create_response(200, {"status": "accepted", "ticket_id": agent_input.ticket_id, "messageId": msg_id})
        parse_error = "SQS send failed"

    # Raw fallback — queue raw payload so no ticket is lost
    raw_message = {"_raw": True, "_parse_error": parse_error, "payload": payload, "event_type": event_type, "ticket_id": ticket_id}
    dedup_id = _dedup_id(raw_body, str(datetime.now(timezone.utc).timestamp()))
    msg_id = sqs.send_message(raw_message, ticket_id, dedup_id)

    if msg_id:
        logger.warning("Queued raw payload ticket_id=%s error=%s", ticket_id, parse_error)
        return create_response(200, {"status": "accepted_raw", "ticket_id": ticket_id, "messageId": msg_id, "warning": parse_error})

    logger.error("Both SQS sends failed ticket_id=%s", ticket_id)
    return create_response(500, {"status": "error", "ticket_id": ticket_id, "message": "Failed to queue message"})


def _resolve_agent_input(
    payload: dict[str, Any],
    event_type: str,
    ticket_id: str,
    config: Config,
) -> tuple[AgentInput | None, str | None]:
    """Build agent input from webhook. Returns (input, error)."""
    if event_type == "created":
        try:
            return WebhookParser.build_agent_input_from_created(payload), None
        except Exception as e:
            logger.exception("Failed to parse created webhook: %s", e)
            return None, str(e)

    if event_type == "updated":
        if ticket_id == "unknown":
            return None, "Updated webhook missing ticket_id"
        if not config.secrets_arn:
            return None, "Freshdesk API not configured (no SECRETS_ARN)"

        creds = SecretsClient().get_freshdesk_credentials(config.secrets_arn)
        if not creds:
            return None, "Failed to retrieve Freshdesk credentials"

        ticket_data = FreshdeskClient(creds, config.api_timeout).fetch_ticket_with_conversations(int(ticket_id))
        if not ticket_data:
            return None, f"Failed to fetch ticket {ticket_id} from Freshdesk API"

        try:
            return TicketParser.ticket_to_agent_input(ticket_data, event_type="updated"), None
        except Exception as e:
            logger.exception("Failed to parse ticket data: %s", e)
            return None, str(e)

    # Unknown event — try as created if description exists
    block = payload.get("freshdesk_webhook") or payload
    if isinstance(block, dict) and (block.get("ticket_description") or block.get("description")):
        try:
            return WebhookParser.build_agent_input_from_created(payload), None
        except Exception as e:
            return None, str(e)

    return None, "Unknown event type and no ticket data in payload"


def _dedup_id(payload: str, salt: str) -> str:
    """SHA-256 deduplication ID for SQS FIFO."""
    return hashlib.sha256((payload + salt).encode()).hexdigest()[:128]
