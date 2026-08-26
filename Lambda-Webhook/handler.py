# Copyright 2025 Loopper-AI
# Lambda: Freshdesk webhook → fetch full ticket from API → format → SQS FIFO.
# Webhook sends ticket_id + triggered_event. Lambda fetches full ticket + conversations.

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Literal

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Module-level clients — reused across warm invocations
_sqs = boto3.client("sqs")
_sm = boto3.client("secretsmanager")

SUPPORT_EMAIL = "support@loopper.com"
UNKNOWN_CUSTOMER = "unknown@customer"

# Pre-compiled regex patterns for noise removal
_RE_IMAGE_TOKENS = re.compile(
    r"\[(?:signatureImage|image:[^\]]*|Loopper|cid:[^\]]*|signature_\d+)\]",
    re.IGNORECASE,
)
_RE_FRESHDESK_ATTACHMENT = re.compile(
    r"\[?https?://eucattachment\.freshdesk\.com/inline/attachment\?token=[A-Za-z0-9_.\-]+\]?"
)
_RE_TRACKING_URLS = re.compile(
    r"https?://(?:loopper\.us\d+\.list-manage\.com|mcusercontent\.com)/\S+"
)
_RE_OUTLOOK_SEP = re.compile(r"^\s*_{10,}\s*$", re.MULTILINE)
_RE_DASH_SEP = re.compile(r"-{3,}")
# Markdown image tags: ![alt](url) or ![alt]() — DOTALL for multiline alt text
_RE_MD_IMAGES = re.compile(r"!\[[^\]]*\]\([^)]*\)", re.DOTALL)
# Empty markdown links / bracket artifacts: []() or [text]()
_RE_EMPTY_MD_LINKS = re.compile(r"\[[^\]]*\]\(\s*\)", re.DOTALL)
# Bold/italic markdown wrappers — keep inner text
_RE_MD_BOLD = re.compile(r"\*{1,3}([^*]+)\*{1,3}")
# Automated email footers (may span line breaks)
_RE_AUTO_FOOTER = re.compile(
    r"This\s+e-?mail\s+was\s+sent\s+automatically\s+by\s+our\s+digital\s+assistant\.?",
    re.IGNORECASE,
)
# Bracketed tracking tokens like [-YJLL6] or [ABC123]
_RE_BRACKET_TOKENS = re.compile(r"\[-?[A-Z0-9]{4,}\]")
# Equal-sign separators
_RE_EQUAL_SEP = re.compile(r"={10,}")
# Angle-bracket URLs/mailto from HTML-to-text conversion: <https://...> <mailto:...>
_RE_ANGLE_URLS = re.compile(r"<(?:https?://|mailto:)[^>]+>")
# Bare mailto: links
_RE_MAILTO = re.compile(r"mailto:\S+")
# All http/https URLs
_RE_HTTP_URLS = re.compile(r"https?://\S+")
# Bare www. URLs
_RE_WWW_URLS = re.compile(r"www\.\S+")
# Phone numbers in signatures: +33(0)4 87 91 46 22
_RE_PHONE_SIG = re.compile(r"\+\d{1,3}\(\d\)\d[\d\s]{8,}")
# French legal/confidentiality disclaimer blocks
_RE_LEGAL_FR = re.compile(
    r"Ce\s+message\s+et\s+toutes?\s+les?\s+pi[eè]ces?\s+jointes?\s+sont\s+confidentiels?.*",
    re.IGNORECASE | re.DOTALL,
)
# English confidentiality disclaimer blocks
_RE_LEGAL_EN = re.compile(
    r"This\s+(?:e-?mail|message)\s+(?:and\s+any\s+attachments?\s+)?(?:is|are)\s+(?:intended\s+)?(?:solely\s+)?(?:for|confidential).*?(?:delete|destroy|discard)\s+(?:it|this|the\s+message).*?$",
    re.IGNORECASE | re.DOTALL,
)
# "Nous contacter : www..." footer lines
_RE_CONTACT_FOOTER = re.compile(r"Nous\s+contacter\s*:\s*\S+", re.IGNORECASE)

# Whitespace-only lines → empty lines (enables blank-line collapsing)
_RE_BLANK_LINES = re.compile(r"^[^\S\n]+$", re.MULTILINE)
# Zero-width characters from HTML-to-text conversion
_RE_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
# Excess whitespace
_RE_EXCESS_NL = re.compile(r"\n{3,}")
_RE_EXCESS_SPACES = re.compile(r"[^\S\n]{3,}")  # 3+ horizontal whitespace

# Quote boundary detection — earliest match wins, marker inserted before it
QUOTE_MARKER = "\n\n[QUOTED THREAD BELOW]\n\n"
_QUOTE_PATTERNS = [
    # French: "Le mer. 25 févr. 2026 à 12:15, ... a écrit :"
    re.compile(r"Le\s+\d{1,2}\s+.{3,80}\s+a\s+[ée]crit\s*:", re.IGNORECASE),
    # English: "On Mon, 9 Feb at 11:49 AM , ... wrote:"
    re.compile(r"^\s*On\s+.{5,120}\s+wrote\s*:.*$", re.MULTILINE | re.IGNORECASE),
    # German: "Am ... schrieb ...:"
    re.compile(r"^\s*Am\s+.{5,120}\s+schrieb\s*.*:.*$", re.MULTILINE | re.IGNORECASE),
    # Dutch: "Op ... schreef ...:"
    re.compile(r"^\s*Op\s+.{5,120}\s+schreef\s*.*:.*$", re.MULTILINE | re.IGNORECASE),
    # Outlook-style header block: "De :" / "From:" / "Van:"
    re.compile(r"^\s*(?:De|From|Van)\s*:\s*\S.+$", re.MULTILINE | re.IGNORECASE),
    # "--- Original Message ---" / "--- Forwarded message ---"
    re.compile(r"^-{2,}\s*(?:Original|Forwarded)\s+(?:Message|message)\s*-{2,}", re.MULTILINE | re.IGNORECASE),
    # Universal "> " quoted lines (3+ consecutive)
    re.compile(r"(?:^\s*>.+\n){3,}", re.MULTILINE),
]


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


def _insert_quote_marker(text: str) -> str:
    """Find the earliest quote boundary and insert [QUOTED THREAD BELOW] marker."""
    if not text:
        return text
    earliest_pos = len(text)
    earliest_match = None
    for pat in _QUOTE_PATTERNS:
        m = pat.search(text)
        if m and m.start() < earliest_pos:
            earliest_pos = m.start()
            earliest_match = m
    if earliest_match is None:
        return text
    before = text[:earliest_pos].rstrip()
    after = text[earliest_pos:]
    return before + QUOTE_MARKER + after


def _smart_clean(text: str) -> str:
    """Strip noise tokens, detect quote boundaries, insert marker."""
    if not text:
        return ""
    # 1. Normalize line endings, tabs, and zero-width chars
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    # Handle LITERAL \r\n, \n, \r strings from Freshdesk HTML-to-text conversion
    text = text.replace("\\r\\n", "\n")
    text = text.replace("\\n", "\n")
    text = text.replace("\\r", "")
    text = _RE_ZERO_WIDTH.sub("", text)
    # 2. Remove noise tokens
    text = _RE_IMAGE_TOKENS.sub("", text)
    text = _RE_FRESHDESK_ATTACHMENT.sub("", text)
    text = _RE_TRACKING_URLS.sub("", text)
    text = _RE_OUTLOOK_SEP.sub("", text)
    text = _RE_DASH_SEP.sub("", text)
    text = _RE_MD_IMAGES.sub("", text)
    text = _RE_EMPTY_MD_LINKS.sub("", text)
    text = _RE_MD_BOLD.sub(r"\1", text)  # unwrap bold, keep text
    text = _RE_AUTO_FOOTER.sub("", text)
    text = _RE_BRACKET_TOKENS.sub("", text)
    text = _RE_EQUAL_SEP.sub("", text)
    text = _RE_ANGLE_URLS.sub("", text)
    text = _RE_MAILTO.sub("", text)
    text = _RE_HTTP_URLS.sub("", text)
    text = _RE_WWW_URLS.sub("", text)
    text = _RE_PHONE_SIG.sub("", text)
    text = _RE_LEGAL_FR.sub("", text)
    text = _RE_LEGAL_EN.sub("", text)
    text = _RE_CONTACT_FOOTER.sub("", text)
    # 3. Insert quote marker BEFORE whitespace cleanup (patterns need line structure)
    text = _insert_quote_marker(text)
    # 4. Strip whitespace-only lines so blank-line collapsing works
    text = _RE_BLANK_LINES.sub("", text)
    # 5. Trim leading/trailing whitespace per line
    text = "\n".join(ln.strip() for ln in text.split("\n"))
    # 6. Clean up excess whitespace
    text = _RE_EXCESS_SPACES.sub(" ", text)
    text = _RE_EXCESS_NL.sub("\n\n", text)
    return text.strip()


# --- Freshdesk API ------------------------------------------------------------


def _get_freshdesk_secrets(secrets_arn: str) -> tuple[str | None, str | None]:
    """Return (base_url, api_key) from Secrets Manager."""
    try:
        r = _sm.get_secret_value(SecretId=secrets_arn)
        data = json.loads(r.get("SecretString", "{}"))
        base_url = (data.get("FRESHDESK_BASE_URL") or "").strip().rstrip("/")
        api_key = (data.get("FRESHDESK_API_KEY") or "").strip()
        return (base_url or None, api_key or None)
    except Exception as e:
        logger.warning("Failed to get Freshdesk secrets: %s", e)
        return None, None


def _freshdesk_get(path: str, base_url: str, api_key: str) -> Any:
    """Generic Freshdesk API GET request."""
    url = f"{base_url}{path}"
    auth = base64.b64encode(f"{api_key}:X".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _fetch_ticket(ticket_id: int, base_url: str, api_key: str) -> dict[str, Any] | None:
    """Fetch ticket with requester (no embedded conversations)."""
    try:
        logger.info("Fetching ticket ticket_id=%s", ticket_id)
        data = _freshdesk_get(f"/api/v2/tickets/{ticket_id}?include=requester", base_url, api_key)
        return data
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        logger.warning("Freshdesk API ticket fetch failed ticket_id=%s: %s", ticket_id, e)
        return None


def _fetch_conversations(ticket_id: int, base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch ALL conversations via dedicated endpoint with pagination (30 per page)."""
    all_convs: list[dict[str, Any]] = []
    page = 1
    while True:
        try:
            data = _freshdesk_get(
                f"/api/v2/tickets/{ticket_id}/conversations?page={page}",
                base_url, api_key,
            )
        except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
            logger.warning("Freshdesk conversations fetch failed ticket_id=%s page=%d: %s", ticket_id, page, e)
            break
        if not isinstance(data, list) or len(data) == 0:
            break
        all_convs.extend(data)
        if len(data) < 30:  # last page
            break
        page += 1
    logger.info("Fetched %d conversations for ticket_id=%s", len(all_convs), ticket_id)
    return all_convs


# --- Ticket formatting --------------------------------------------------------


def _build_message(index: int, timestamp: str, sender: str, recipient: str,
                   body: str, direction: Literal["incoming", "outgoing"]) -> dict[str, Any]:
    return {
        "message_index": index,
        "timestamp": timestamp,
        "sender_email": sender,
        "recipient": recipient,
        "clean_body": body,
        "direction": direction,
        "language": None,
    }


def _ticket_to_agent_input(ticket_data: dict[str, Any], conversations: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert Freshdesk ticket + dedicated conversations into agent input format."""
    ticket_id_str = str(ticket_data.get("id") or "")
    requester_email = (ticket_data.get("requester") or {}).get("email") or UNKNOWN_CUSTOMER
    subject = (ticket_data.get("subject") or "").strip()

    # Use description_text (plain text) + smart clean
    desc_body = _smart_clean(ticket_data.get("description_text") or "")
    if not desc_body:
        desc_body = subject or "(No description provided)"

    created_at = ticket_data.get("created_at") or datetime.now(timezone.utc).isoformat()
    updated_at = ticket_data.get("updated_at") or created_at

    # to_emails is the address(es) the customer actually wrote to — Freshdesk
    # returns it on the ticket resource itself, same as on conversation entries.
    ticket_to_emails = ticket_data.get("to_emails") or []
    initial_recipient = ticket_to_emails[0] if ticket_to_emails else SUPPORT_EMAIL

    messages = [_build_message(0, created_at, requester_email, initial_recipient, desc_body, "incoming")]
    conversation_flow: list[str] = ["incoming"]

    # Filter to public only, sort by created_at
    public = sorted(
        (c for c in conversations if not c.get("private")),
        key=lambda c: c.get("created_at") or "",
    )

    for conv in public:
        # Use body_text (plain text from Freshdesk) + smart noise stripping
        clean = _smart_clean(conv.get("body_text") or "")
        if not clean:
            continue

        incoming = conv.get("incoming", True)
        direction: Literal["incoming", "outgoing"] = "incoming" if incoming else "outgoing"
        from_email = conv.get("from_email") or (requester_email if incoming else SUPPORT_EMAIL)
        to_emails = conv.get("to_emails") or []

        messages.append(_build_message(
            len(messages),
            conv.get("created_at") or updated_at,
            from_email,
            to_emails[0] if to_emails else SUPPORT_EMAIL,
            clean,
            direction,
        ))
        conversation_flow.append(direction)

    incoming_count = sum(1 for m in messages if m["direction"] == "incoming")
    return {"ticket": {
        "ticket_id": ticket_id_str,
        "subject": subject,
        "messages": messages,
        "message_count": len(messages),
        "started_at": created_at,
        "last_updated_at": updated_at,
        "duration_hours": 0.0,
        "languages": [],
        "incoming_count": incoming_count,
        "outgoing_count": len(messages) - incoming_count,
        "conversation_flow": conversation_flow,
        # Freshdesk's own agent id — None when unassigned. Lets app-server detect
        # a manual Freshdesk-side assignment during its assignment-wait race.
        "responder_id": ticket_data.get("responder_id"),
    }}


# --- SQS FIFO ----------------------------------------------------------------


def _dedup_id(*parts: str) -> str:
    return hashlib.sha256("".join(parts).encode()).hexdigest()[:128]


def _send_to_sqs(queue_url: str, body: dict[str, Any], ticket_id: str, dedup: str) -> str | None:
    """Send message to SQS FIFO. Returns message_id or None."""
    try:
        r = _sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(body, default=str),
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


def _resolve_ticket(ticket_id: str, event_type: str, secrets_arn: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch and format the ticket. Returns (resolved_message, error)."""
    base_url, api_key = _get_freshdesk_secrets(secrets_arn) if secrets_arn else (None, None)
    if not base_url or not api_key:
        return None, "Freshdesk API not configured"
    if ticket_id == "unknown":
        return None, "Webhook missing ticket_id"

    tid = int(ticket_id)
    ticket_data = _fetch_ticket(tid, base_url, api_key)
    if ticket_data is None:
        return None, "Failed to fetch ticket from Freshdesk API"

    conversations = _fetch_conversations(tid, base_url, api_key)
    agent = _ticket_to_agent_input(ticket_data, conversations)
    resolved = {"event_type": event_type, "ticket_id": agent["ticket"]["ticket_id"], **agent}
    return resolved, None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    queue_url = os.environ.get("QUEUE_URL")
    secrets_arn = os.environ.get("SECRETS_ARN")
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

    # Try to resolve full ticket from Freshdesk API → SQS
    resolved, error_msg = _resolve_ticket(ticket_id, event_type, secrets_arn)
    if resolved is not None:
        dedup = _dedup_id(raw_body, request_id)
        msg_id = _send_to_sqs(queue_url, resolved, resolved["ticket_id"], dedup)
        if msg_id:
            return _response(200, {"status": "accepted", "ticket_id": resolved["ticket_id"], "messageId": msg_id})
        error_msg = "SQS send failed"

    # Fallback: queue raw payload so no ticket is lost
    raw_message = {
        "_raw": True,
        "_parse_error": error_msg,
        "payload": payload,
        "event_type": event_type,
        "ticket_id": ticket_id,
    }
    dedup = _dedup_id(raw_body, str(datetime.now(timezone.utc).timestamp()))
    logger.info("Raw fallback ticket_id=%s error=%s", ticket_id, error_msg)
    msg_id = _send_to_sqs(queue_url, raw_message, ticket_id, dedup)
    if msg_id:
        return _response(200, {"status": "accepted_raw", "ticket_id": ticket_id, "messageId": msg_id, "warning": error_msg})

    logger.error("All SQS sends failed ticket_id=%s", ticket_id)
    return _response(500, {"status": "error", "ticket_id": ticket_id, "message": "Failed to queue message"})
