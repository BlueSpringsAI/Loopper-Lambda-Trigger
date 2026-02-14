# Copyright 2025 Loopper-AI
# Freshdesk ticket API response parser

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..models import AgentInput, Message, MessageDirection, Ticket
from ..utils.html_utils import clean_html_body

logger = logging.getLogger(__name__)


class TicketParser:
    """Parser for Freshdesk ticket API responses."""

    @staticmethod
    def ticket_to_agent_input(ticket_data: dict[str, Any], event_type: str = "updated") -> AgentInput:
        """Convert Freshdesk API ticket response to agent input with full conversation history."""
        ticket_id = str(ticket_data.get("id") or "unknown")
        requester = ticket_data.get("requester") or {}
        requester_email = requester.get("email") or "unknown@customer"
        subject = ticket_data.get("subject") or ""
        raw_desc = ticket_data.get("description") or ticket_data.get("description_text") or ""
        desc_body = clean_html_body(raw_desc or subject) or "(No description provided)"
        created_at = ticket_data.get("created_at") or datetime.now(timezone.utc).isoformat()
        updated_at = ticket_data.get("updated_at") or created_at

        # First message: the ticket description
        messages: list[Message] = [
            Message(
                message_index=0,
                timestamp=created_at,
                sender_email=requester_email,
                recipient="support@loopper.com",
                clean_body=desc_body,
                direction="incoming",
            )
        ]

        # Append public conversations
        conversations = ticket_data.get("conversations") or []
        if isinstance(conversations, list):
            public = sorted(
                [c for c in conversations if not c.get("private", False)],
                key=lambda c: c.get("created_at") or "",
            )
            for conv in public:
                msg = _parse_conversation(conv, requester_email, len(messages))
                if msg:
                    messages.append(msg)

        ticket = Ticket(
            ticket_id=ticket_id,
            messages=messages,
            started_at=created_at,
            last_updated_at=updated_at,
        )

        logger.info("Parsed ticket %s: %d messages", ticket_id, ticket.message_count)
        return AgentInput(event_type=event_type, ticket_id=ticket_id, ticket=ticket)


def _parse_conversation(conv: dict[str, Any], default_requester: str, index: int) -> Message | None:
    """Parse a single Freshdesk conversation entry. Returns None if empty."""
    raw = conv.get("body_text") or conv.get("body") or ""
    clean = clean_html_body(raw)
    if not clean:
        return None

    incoming = conv.get("incoming", True)
    direction: MessageDirection = "incoming" if incoming else "outgoing"
    from_email = conv.get("from_email") or (default_requester if incoming else "support@loopper.com")
    to_emails = conv.get("to_emails") or []
    recipient = to_emails[0] if to_emails else "support@loopper.com"

    return Message(
        message_index=index,
        timestamp=conv.get("created_at") or datetime.now(timezone.utc).isoformat(),
        sender_email=from_email,
        recipient=recipient,
        clean_body=clean,
        direction=direction,
    )
