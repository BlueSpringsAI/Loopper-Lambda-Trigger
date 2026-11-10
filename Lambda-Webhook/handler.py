# Copyright 2025 Loopper-AI
# Lambda: Freshdesk webhook → SQS FIFO, as-is.
# It used to fetch the full ticket from the Freshdesk API too, but app-server
# fetches it again anyway, and the duplicate reads ran the account's 100/min
# limit out. app-server is now the only Freshdesk caller (one shared budget).

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from typing import Any, Literal

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-level client — reused across warm invocations
_sqs = boto3.client("sqs")


# --- Parsing ------------------------------------------------------------------


def _get_webhook_block(payload: dict[str, Any]) -> dict[str, Any]:
    block = payload.get("freshdesk_webhook") or payload
    return block if isinstance(block, dict) else {}


def get_event_type(payload: dict[str, Any]) -> Literal["created", "updated", "unknown"]:
    triggered = (_get_webhook_block(payload).get("triggered_event") or "").lower()
    if "created" in triggered:
        return "created"
    if "update" in triggered:
        return "updated"
    return "unknown"


def _extract_ticket_id(payload: dict[str, Any]) -> str:
    block = _get_webhook_block(payload)
    return str(block.get("ticket_id") or block.get("id") or "unknown")


# --- SQS FIFO ----------------------------------------------------------------


def _dedup_id(*parts: str) -> str:
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:128]


# SQS rejects bodies of 256 KB or more; keep some headroom.
SQS_MAX_BYTES = 250 * 1024
# Payload values larger than this are dropped when the message is too big.
TRIM_FIELD_BYTES = 2048


def _fit_message(message: dict[str, Any], ticket_id: str) -> str:
    """JSON body under the SQS limit (#8).

    app-server only reads the envelope's event_type/ticket_id and fetches the
    ticket from Freshdesk itself, so the payload's large fields (description,
    HTML) can be dropped from an oversized webhook without losing anything.
    """
    body = json.dumps(message, default=str)
    if len(body.encode()) <= SQS_MAX_BYTES:
        return body
    payload = message.get("payload") or {}
    block_key = "freshdesk_webhook" if isinstance(payload.get("freshdesk_webhook"), dict) else None
    block = payload[block_key] if block_key else payload
    small = {k: v for k, v in block.items() if len(json.dumps(v, default=str).encode()) <= TRIM_FIELD_BYTES}
    trimmed = {block_key: small} if block_key else small
    logger.warning(
        "Webhook %d bytes over SQS limit — dropped large payload fields %s ticket_id=%s",
        len(body.encode()), sorted(set(block) - set(small)), ticket_id,
    )
    body = json.dumps({**message, "payload": trimmed, "payload_trimmed": True}, default=str)
    if len(body.encode()) > SQS_MAX_BYTES:  # still too big: the envelope alone is enough
        body = json.dumps({**message, "payload": {}, "payload_trimmed": True}, default=str)
    return body


def _send_to_sqs(queue_url: str, body: dict[str, Any], ticket_id: str, dedup: str) -> str | None:
    """Send message to SQS FIFO. Returns message_id or None."""
    try:
        r = _sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=_fit_message(body, ticket_id),
            MessageGroupId=ticket_id or "default",
            MessageDeduplicationId=dedup,
        )
        return r.get("MessageId")
    except Exception as e:
        logger.exception("SQS SendMessage failed: %s", e)
        return None


# --- Lambda handler -----------------------------------------------------------


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _parse_body(event: dict[str, Any]) -> dict[str, Any] | None:
    """Decode and parse the API Gateway request body."""
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _should_skip(event_type: str) -> bool:
    env_map = {"created": "PROCESS_CREATED", "updated": "PROCESS_UPDATED"}
    env_key = env_map.get(event_type)
    if not env_key:
        return False
    return os.environ.get(env_key, "true").lower() not in ("1", "true", "yes")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    queue_url = os.environ.get("QUEUE_URL")
    if not queue_url:
        logger.error("QUEUE_URL not set")
        return _response(500, {"status": "error", "message": "QUEUE_URL not configured"})

    payload = _parse_body(event)
    if payload is None:
        return _response(400, {"status": "rejected", "message": "Invalid JSON body"})

    ticket_id = _extract_ticket_id(payload)
    event_type = get_event_type(payload)
    logger.info("event_type=%s ticket_id=%s", event_type, ticket_id)

    if _should_skip(event_type):
        return _response(200, {"status": "skipped", "ticket_id": ticket_id, "reason": f"{event_type} not processed"})

    request_id = event.get("requestContext", {}).get("requestId", "")
    raw_body = event.get("body") or "{}"

    # app-server reads event_type/ticket_id from this envelope and fetches the
    # ticket itself; if Freshdesk is rate limited it fails and SQS redelivers.
    # Same shape as the old raw fallback, which app-server already handled.
    message = {
        "_raw": True,
        "payload": payload,
        "event_type": event_type,
        "ticket_id": ticket_id,
    }
    msg_id = _send_to_sqs(queue_url, message, ticket_id, _dedup_id(raw_body, request_id))
    if msg_id:
        return _response(200, {"status": "accepted", "ticket_id": ticket_id, "messageId": msg_id})

    logger.error("SQS send failed ticket_id=%s", ticket_id)
    return _response(500, {"status": "error", "ticket_id": ticket_id, "message": "Failed to queue message"})
