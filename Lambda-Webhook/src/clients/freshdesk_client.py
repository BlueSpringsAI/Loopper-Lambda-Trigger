# Copyright 2025 Loopper-AI
# Freshdesk API client

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from ..models import FreshdeskCredentials

logger = logging.getLogger(__name__)


class FreshdeskClient:
    """Client for Freshdesk API operations."""

    def __init__(self, credentials: FreshdeskCredentials, timeout: float = 30.0):
        self.credentials = credentials
        self.timeout = timeout

    def fetch_ticket_with_conversations(self, ticket_id: int) -> dict[str, Any] | None:
        """Fetch ticket with conversations and requester from Freshdesk API.

        Needed for updated webhooks which lack full conversation history.
        """
        url = f"{self.credentials.base_url}/api/v2/tickets/{ticket_id}?include=conversations,requester"

        # Freshdesk API: Basic Auth with API key as username, "X" as password
        token = base64.b64encode(f"{self.credentials.api_key}:X".encode()).decode()
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {token}")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.error("Freshdesk API error for ticket %s: %s %s", ticket_id, e.code, e.reason)
        except urllib.error.URLError as e:
            logger.error("Freshdesk API connection error for ticket %s: %s", ticket_id, e.reason)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON from Freshdesk for ticket %s: %s", ticket_id, e)

        return None
