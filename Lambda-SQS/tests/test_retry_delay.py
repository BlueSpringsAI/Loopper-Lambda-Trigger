"""Deployed forwarder (Lambda-SQS/handler.py): failed messages retry 90, 120, 150, 180 … s apart."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

_spec = importlib.util.spec_from_file_location("forwarder", Path(__file__).resolve().parents[1] / "handler.py")
forwarder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(forwarder)

ARN = "arn:aws:sqs:eu-central-1:363551011685:loopper-ai-staging-freshdesk-queue.fifo"
URL = "https://sqs.eu-central-1.amazonaws.com/363551011685/loopper-ai-staging-freshdesk-queue.fifo"


def _event(receive_count: str = "1") -> dict:
    return {"Records": [{"messageId": "m1", "receiptHandle": "rh1", "body": "{}", "eventSourceARN": ARN,
                         "attributes": {"ApproximateReceiveCount": receive_count}}]}


def test_schedule_is_90_then_plus_30_per_failure():
    assert [forwarder.retry_delay(n) for n in range(1, 11)] == [90, 120, 150, 180, 210, 240, 270, 300, 330, 360]
    assert forwarder.retry_delay(0) == 90 and forwarder.retry_delay(100) == 900  # capped
    assert sum(forwarder.retry_delay(n) for n in range(1, 10)) == 1890  # ~31.5 min over 10 tries


def test_queue_url_from_arn():
    assert forwarder._queue_url(ARN) == ("eu-central-1", URL)


def _run(event, *, fail=True):
    sqs = MagicMock()
    boto3 = MagicMock(client=MagicMock(return_value=sqs))
    if fail:
        err = urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b"busy"))
        opener = patch.object(forwarder.urllib.request, "urlopen", side_effect=err)
    else:
        resp = MagicMock(); resp.getcode.return_value = 202; resp.read.return_value = b"{}"
        resp.__enter__ = lambda s: s; resp.__exit__ = lambda *a: False
        opener = patch.object(forwarder.urllib.request, "urlopen", return_value=resp)
    with patch.dict("os.environ", {"APP_WEBHOOK_URL": "http://app/queue/webhook"}), \
         patch.dict(sys.modules, {"boto3": boto3}), opener:
        out = forwarder.lambda_handler(event, None)
    return out, sqs


def test_failed_message_gets_its_next_delay():
    out, sqs = _run(_event("3"))
    assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}
    sqs.change_message_visibility.assert_called_once_with(QueueUrl=URL, ReceiptHandle="rh1", VisibilityTimeout=150)


def test_success_changes_nothing():
    out, sqs = _run(_event("2"), fail=False)
    assert "batchItemFailures" not in out
    sqs.change_message_visibility.assert_not_called()


def test_sqs_error_still_reports_the_failure():
    sqs_err = MagicMock(change_message_visibility=MagicMock(side_effect=RuntimeError("AccessDenied")))
    with patch.dict("os.environ", {"APP_WEBHOOK_URL": "http://app/queue/webhook"}), \
         patch.dict(sys.modules, {"boto3": MagicMock(client=MagicMock(return_value=sqs_err))}), \
         patch.object(forwarder.urllib.request, "urlopen",
                      side_effect=urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b""))):
        out = forwarder.lambda_handler(_event("1"), None)
    assert out == {"batchItemFailures": [{"itemIdentifier": "m1"}]}  # falls back to the queue's 90 s
