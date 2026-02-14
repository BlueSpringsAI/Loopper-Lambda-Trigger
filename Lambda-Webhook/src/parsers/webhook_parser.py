# Copyright 2025 Loopper-AI
# Freshdesk webhook payload parser

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import AgentInput, EventType, Message, Ticket
from ..utils.html_utils import clean_html_body


def _get_block(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the freshdesk_webhook block, falling back to the payload itself."""
    block = payload.get("freshdesk_webhook") or payload
    return block if isinstance(block, dict) else {}


class WebhookParser:
    """Parser for Freshdesk webhook payloads."""

    @staticmethod
    def get_event_type(payload: dict[str, Any]) -> EventType:
        """Determine event type from triggered_event field."""
        triggered = (_get_block(payload).get("triggered_event") or "").lower()
        if "created" in triggered:
            return "created"
        if "update" in triggered:
            return "updated"
        return "unknown"

    @staticmethod
    def extract_ticket_id(payload: dict[str, Any]) -> str:
        """Extract ticket ID as string."""
        block = _get_block(payload)
        ticket_id = block.get("ticket_id") or block.get("id")
        return str(ticket_id) if ticket_id is not None else "unknown"

    @staticmethod
    def build_agent_input_from_created(payload: dict[str, Any]) -> AgentInput:
        """Build agent input from a created webhook (no API call needed)."""
        block = _get_block(payload)

        ticket_id = str(block.get("ticket_id") or block.get("id") or "unknown")
        raw_desc = block.get("ticket_description") or block.get("description") or ""
        requester_email = block.get("ticket_contact_email") or block.get("requester_email") or "unknown@customer"
        subject = block.get("ticket_subject") or block.get("subject") or ""

        body = clean_html_body(raw_desc or subject) or "(No description provided)"
        now = datetime.now(timezone.utc).isoformat()

        message = Message(
            message_index=0,
            timestamp=now,
            sender_email=requester_email,
            recipient="support@loopper.com",
            clean_body=body,
            direction="incoming",
        )

        ticket = Ticket(
            ticket_id=ticket_id,
            messages=[message],
            started_at=now,
            last_updated_at=now,
        )

        return AgentInput(event_type="created", ticket_id=ticket_id, ticket=ticket)
