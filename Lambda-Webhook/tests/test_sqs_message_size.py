"""api-to-sqs keeps every message under the SQS 256 KB limit (#8)."""
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("AWS_DEFAULT_REGION", "eu-central-1")
_spec = importlib.util.spec_from_file_location("webhook_handler", Path(__file__).parents[1] / "handler.py")
handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(handler)


def _send(webhook):
    handler._sqs = MagicMock(send_message=MagicMock(return_value={"MessageId": "m1"}))
    event = {"body": json.dumps(webhook), "requestContext": {"requestId": "r1"}}
    os.environ["QUEUE_URL"] = "https://sqs/test.fifo"
    resp = handler.lambda_handler(event, None)
    sent = handler._sqs.send_message.call_args.kwargs["MessageBody"]
    return resp, sent, json.loads(sent)


def test_small_webhook_is_forwarded_unchanged():
    webhook = {"freshdesk_webhook": {"ticket_id": "1", "triggered_event": "ticket_updated", "ticket_subject": "Hi"}}
    resp, _, msg = _send(webhook)
    assert resp["statusCode"] == 200 and msg["payload"] == webhook and "payload_trimmed" not in msg


def test_oversized_webhook_is_trimmed_and_still_queued():
    webhook = {"freshdesk_webhook": {"ticket_id": "808342", "triggered_event": "ticket_updated",
                                     "ticket_subject": "Order", "ticket_description": "x" * 400_000}}
    resp, raw, msg = _send(webhook)
    assert resp["statusCode"] == 200
    assert len(raw.encode()) <= handler.SQS_MAX_BYTES
    assert msg["ticket_id"] == "808342" and msg["event_type"] == "updated" and msg["payload_trimmed"]
    assert msg["payload"]["freshdesk_webhook"] == {"ticket_id": "808342", "triggered_event": "ticket_updated",
                                                    "ticket_subject": "Order"}


def test_many_medium_fields_fall_back_to_envelope_only():
    block = {"ticket_id": "9", "triggered_event": "ticket_created", **{f"f{i}": "y" * 2000 for i in range(200)}}
    _, raw, msg = _send({"freshdesk_webhook": block})
    assert len(raw.encode()) <= handler.SQS_MAX_BYTES
    assert msg["payload"] == {} and msg["ticket_id"] == "9" and msg["event_type"] == "created"
