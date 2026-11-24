"""
SQS → Lambda (event source mapping) → POST to app-server at /queue/webhook.

Exact POST request:
  Method:  POST
  URL:     APP_WEBHOOK_URL (e.g. http://<ALB-dns>/queue/webhook)
  Headers: Content-Type: application/json
  Body:    JSON object with "Records" key (SQS event shape), e.g. {"Records": [{ "messageId", "body", ... }]}.
           App expects an object; it extracts Freshdesk payload from first record's body.

App returns 202 Accepted and processes in background. Lambda uses ReportBatchItemFailures
so only failed messages are retried (then sent to main queue DLQ). Each failed message is
retried 90 s later, then 120, 150, 180 … (see retry_delay), not on the queue's fixed timeout.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

# App returns 202 quickly; short timeout is enough
REQUEST_TIMEOUT = 15

# Max bytes of body to log (avoid PII/secrets in logs)
LOG_BODY_TRUNCATE = 500

# Retry spacing for failed messages: 90 s after the 1st failure, then +30 s per
# failure (90, 120, 150, 180 …), instead of the queue's fixed visibility timeout.
# With maxReceiveCount 10 that is ~31 min of tries before the DLQ (was ~15 min).
RETRY_BASE_S = 90
RETRY_STEP_S = 30
RETRY_MAX_S = 900


def retry_delay(receive_count: int) -> int:
    """Seconds until the next try, after the ``receive_count``-th delivery failed."""
    return min(RETRY_BASE_S + RETRY_STEP_S * (max(receive_count, 1) - 1), RETRY_MAX_S)


def _queue_url(arn: str) -> tuple[str, str]:
    """'arn:aws:sqs:eu-central-1:123:q.fifo' → (region, 'https://sqs.eu-central-1.amazonaws.com/123/q.fifo')."""
    _, _, _, region, account, name = arn.split(":", 5)
    return region, f"https://sqs.{region}.amazonaws.com/{account}/{name}"


def _back_off(records: list[dict[str, Any]]) -> None:
    """Hide each failed message for retry_delay(receive count). Best effort: on any
    error the message just comes back after the queue's visibility timeout."""
    try:
        import boto3
        from botocore.config import Config
    except Exception:  # pragma: no cover — boto3 ships with the Lambda runtime
        return
    clients: dict[str, Any] = {}
    for rec in records:
        try:
            region, url = _queue_url(rec["eventSourceARN"])
            count = int((rec.get("attributes") or {}).get("ApproximateReceiveCount", "1"))
            delay = retry_delay(count)
            if region not in clients:
                clients[region] = boto3.client(
                    "sqs", region_name=region,
                    config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1}),
                )
            clients[region].change_message_visibility(
                QueueUrl=url, ReceiptHandle=rec["receiptHandle"], VisibilityTimeout=delay,
            )
            logger.info("retry in %ds (receive %d) message_id=%s", delay, count, rec.get("messageId"))
        except Exception as e:
            logger.warning("could not set retry delay for message_id=%s: %s", rec.get("messageId"), e)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    SQS event source mapping invokes with { "Records": [ ... ] }.
    POST to APP_WEBHOOK_URL (/queue/webhook). On 2xx (e.g. 202) success, return no batchItemFailures.
    On failure, return batchItemFailures so only failed messages are retried.
    """
    request_id = getattr(context, "aws_request_id", "") if context else ""
    logger.info("lambda_handler started request_id=%s", request_id)

    # OLD: base_url from APP_SERVER_URL, url = base_url + "/eventbridge/sqs"
    # NEW: APP_WEBHOOK_URL = full URL to /queue/webhook, or APP_SERVER_URL + /queue/webhook
    webhook_url = (os.environ.get("APP_WEBHOOK_URL") or "").strip().rstrip("/")
    if not webhook_url:
        base = (os.environ.get("APP_SERVER_URL") or "").strip().rstrip("/")
        if not base:
            logger.error("APP_WEBHOOK_URL and APP_SERVER_URL not set")
            raise ValueError("APP_WEBHOOK_URL or APP_SERVER_URL required")
        webhook_url = f"{base}/queue/webhook"

    # App /queue/webhook expects a JSON object (e.g. {"Records": [...]}), not a raw array.
    if isinstance(event, dict) and "Records" in event and isinstance(event.get("Records"), list):
        records = event["Records"]
        payload_to_send = event  # send full SQS event object so app gets {"Records": [...]}
    else:
        records = []
        payload_to_send = event if isinstance(event, dict) else {"Records": []}

    body = json.dumps(payload_to_send, default=str).encode("utf-8")
    message_ids = [r.get("messageId") for r in records if isinstance(r, dict) and r.get("messageId")]

    logger.info(
        "POST request: method=POST url=%s headers={Content-Type: application/json} body_size=%d record_count=%d message_ids=%s",
        webhook_url,
        len(body),
        len(records),
        message_ids,
    )
    if body and logger.isEnabledFor(logging.DEBUG):
        logger.debug("POST body (truncated): %s", body[:LOG_BODY_TRUNCATE].decode("utf-8", errors="replace"))

    req = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    failed_ids: list[dict[str, str]] = []

    try:
        with urllib.request.urlopen(
            req,
            timeout=REQUEST_TIMEOUT,
            context=ssl.create_default_context(),
        ) as resp:
            code = resp.getcode()
            resp_body = resp.read().decode("utf-8")
            logger.info(
                "app-server response: status=%s content_length=%d request_id=%s message_ids=%s",
                code,
                len(resp_body),
                request_id,
                message_ids,
            )
            # 2xx (e.g. 202 Accepted) = success; no batchItemFailures
            if 200 <= code < 300:
                try:
                    out = json.loads(resp_body) if resp_body.strip() else {}
                    logger.info("success request_id=%s message_ids=%s", request_id, message_ids)
                    return out
                except json.JSONDecodeError:
                    return {"status": code, "body": resp_body[:200]}
            # Non-2xx: report all records as failed so they are retried then go to DLQ
            logger.warning("non-2xx status=%s request_id=%s message_ids=%s", code, request_id, message_ids)
            for rec in records:
                if isinstance(rec, dict) and "messageId" in rec:
                    failed_ids.append({"itemIdentifier": rec["messageId"]})
    except urllib.error.HTTPError as e:
        err_body = e.read()[:500].decode("utf-8", errors="replace")
        logger.error(
            "app-server HTTPError: code=%s body=%s request_id=%s message_ids=%s",
            e.code,
            err_body,
            request_id,
            message_ids,
        )
        for rec in records:
            if isinstance(rec, dict) and "messageId" in rec:
                failed_ids.append({"itemIdentifier": rec["messageId"]})
    except urllib.error.URLError as e:
        logger.exception(
            "POST URLError: reason=%s request_id=%s message_ids=%s",
            e.reason,
            request_id,
            message_ids,
        )
        for rec in records:
            if isinstance(rec, dict) and "messageId" in rec:
                failed_ids.append({"itemIdentifier": rec["messageId"]})
    except Exception as e:
        logger.exception("Unexpected error: %s request_id=%s message_ids=%s", e, request_id, message_ids)
        for rec in records:
            if isinstance(rec, dict) and "messageId" in rec:
                failed_ids.append({"itemIdentifier": rec["messageId"]})

    if failed_ids:
        failed = {f["itemIdentifier"] for f in failed_ids}
        _back_off([r for r in records if isinstance(r, dict) and r.get("messageId") in failed])
        logger.warning("returning batchItemFailures count=%d request_id=%s", len(failed_ids), request_id)
        return {"batchItemFailures": failed_ids}
    return {}
