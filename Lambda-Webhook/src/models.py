# Copyright 2025 Loopper-AI
# Data models for Lambda-Webhook

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal["created", "updated", "unknown"]
MessageDirection = Literal["incoming", "outgoing"]


@dataclass
class Message:
    """Single message in a ticket conversation."""

    message_index: int
    timestamp: str
    sender_email: str
    recipient: str
    clean_body: str
    direction: MessageDirection
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_index": self.message_index,
            "timestamp": self.timestamp,
            "sender_email": self.sender_email,
            "recipient": self.recipient,
            "clean_body": self.clean_body,
            "direction": self.direction,
            "language": self.language,
        }


@dataclass
class Ticket:
    """Ticket with its conversation history."""

    ticket_id: str
    messages: list[Message]
    started_at: str
    last_updated_at: str

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def incoming_count(self) -> int:
        return sum(1 for m in self.messages if m.direction == "incoming")

    @property
    def outgoing_count(self) -> int:
        return self.message_count - self.incoming_count

    @property
    def conversation_flow(self) -> list[str]:
        return [m.direction for m in self.messages]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "messages": [m.to_dict() for m in self.messages],
            "message_count": self.message_count,
            "started_at": self.started_at,
            "last_updated_at": self.last_updated_at,
            "duration_hours": 0.0,
            "languages": [],
            "incoming_count": self.incoming_count,
            "outgoing_count": self.outgoing_count,
            "conversation_flow": self.conversation_flow,
        }


@dataclass
class AgentInput:
    """Complete agent input payload sent to SQS."""

    event_type: EventType
    ticket_id: str
    ticket: Ticket

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "ticket_id": self.ticket_id,
            "ticket": self.ticket.to_dict(),
        }


@dataclass
class FreshdeskCredentials:
    """Freshdesk API credentials."""

    base_url: str
    api_key: str
