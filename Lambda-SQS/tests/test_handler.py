# Copyright 2025 Loopper-AI
# Tests for Lambda-SQS handler

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _make_sqs_event(records: list[dict] | None = None) -> dict:
    """Build a minimal SQS event for testing."""
    if records is None:
        records = [
            {
                "messageId": "msg-001",
                "receiptHandle": "handle-001",
                "body": json.dumps({"event_type": "created", "ticket_id": "123"}),
                "attributes": {},
                "messageAttributes": {},
                "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:test-queue.fifo",
            }
        ]
    return {"Records": records}


def _make_context(request_id: str = "test-req-001") -> MagicMock:
    ctx = MagicMock()
    ctx.aws_request_id = request_id
    return ctx


class TestLambdaHandler:
    """Test suite for lambda_handler."""

    @patch.dict("os.environ", {"APP_WEBHOOK_URL": "http://localhost:8000/queue/webhook"})
    @patch("src.clients.http_client.urllib.request.urlopen")
    def test_success_2xx(self, mock_urlopen):
        """App returns 202 → no batchItemFailures."""
        from src.handler import lambda_handler

        resp_mock = MagicMock()
        resp_mock.getcode.return_value = 202
        resp_mock.read.return_value = b'{"status": "accepted"}'
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp_mock

        result = lambda_handler(_make_sqs_event(), _make_context())

        assert "batchItemFailures" not in result
        assert result.get("status") == "accepted"

    @patch.dict("os.environ", {"APP_WEBHOOK_URL": "http://localhost:8000/queue/webhook"})
    @patch("src.clients.http_client.urllib.request.urlopen")
    def test_non_2xx_returns_batch_failures(self, mock_urlopen):
        """App returns 500 → all records reported as failures."""
        from src.handler import lambda_handler

        resp_mock = MagicMock()
        resp_mock.getcode.return_value = 500
        resp_mock.read.return_value = b"Internal Server Error"
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp_mock

        event = _make_sqs_event()
        result = lambda_handler(event, _make_context())

        assert "batchItemFailures" in result
        ids = [f["itemIdentifier"] for f in result["batchItemFailures"]]
        assert "msg-001" in ids

    @patch.dict("os.environ", {"APP_WEBHOOK_URL": "http://localhost:8000/queue/webhook"})
    @patch("src.clients.http_client.urllib.request.urlopen")
    def test_url_error_returns_batch_failures(self, mock_urlopen):
        """Network error → all records reported as failures."""
        import urllib.error

        from src.handler import lambda_handler

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        result = lambda_handler(_make_sqs_event(), _make_context())

        assert "batchItemFailures" in result
        assert len(result["batchItemFailures"]) == 1

    def test_missing_config_raises(self):
        """No URL configured → ValueError."""
        from src.handler import lambda_handler

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="APP_WEBHOOK_URL"):
                lambda_handler(_make_sqs_event(), _make_context())

    @patch.dict("os.environ", {"APP_WEBHOOK_URL": "http://localhost:8000/queue/webhook"})
    @patch("src.clients.http_client.urllib.request.urlopen")
    def test_empty_records(self, mock_urlopen):
        """Event with empty Records list → still forwards, no failures."""
        from src.handler import lambda_handler

        resp_mock = MagicMock()
        resp_mock.getcode.return_value = 202
        resp_mock.read.return_value = b"{}"
        resp_mock.__enter__ = MagicMock(return_value=resp_mock)
        resp_mock.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp_mock

        result = lambda_handler({"Records": []}, _make_context())
        assert "batchItemFailures" not in result


class TestSQSEventParser:
    """Test suite for SQSEventParser."""

    def test_extract_records(self):
        from src.parsers import SQSEventParser

        event = _make_sqs_event()
        records = SQSEventParser.extract_records(event)

        assert len(records) == 1
        assert records[0].message_id == "msg-001"

    def test_extract_records_skips_malformed(self):
        from src.parsers import SQSEventParser

        event = {"Records": [{"no_id": True}, {"messageId": "good-1", "body": "{}"}]}
        records = SQSEventParser.extract_records(event)

        assert len(records) == 1
        assert records[0].message_id == "good-1"

    def test_extract_records_no_records_key(self):
        from src.parsers import SQSEventParser

        records = SQSEventParser.extract_records({})
        assert records == []

    def test_build_forward_payload(self):
        from src.parsers import SQSEventParser

        event = _make_sqs_event()
        payload = SQSEventParser.build_forward_payload(event)
        parsed = json.loads(payload)

        assert "Records" in parsed
        assert len(parsed["Records"]) == 1


class TestModels:
    """Test suite for data models."""

    def test_sqs_record_from_event_record(self):
        from src.models import SQSRecord

        raw = {"messageId": "m1", "body": '{"a":1}', "receiptHandle": "rh1"}
        record = SQSRecord.from_event_record(raw)

        assert record is not None
        assert record.message_id == "m1"
        assert record.body == '{"a":1}'

    def test_sqs_record_missing_id_returns_none(self):
        from src.models import SQSRecord

        record = SQSRecord.from_event_record({"body": "x"})
        assert record is None

    def test_batch_item_failure_to_dict(self):
        from src.models import BatchItemFailure

        f = BatchItemFailure(item_identifier="msg-123")
        assert f.to_dict() == {"itemIdentifier": "msg-123"}


class TestConfig:
    """Test suite for Config."""

    @patch.dict("os.environ", {"APP_WEBHOOK_URL": "http://app/queue/webhook"})
    def test_from_environment_direct_url(self):
        from src.config import Config

        cfg = Config.from_environment()
        assert cfg.webhook_url == "http://app/queue/webhook"
        valid, err = cfg.validate()
        assert valid is True

    @patch.dict("os.environ", {"APP_SERVER_URL": "http://app"}, clear=True)
    def test_from_environment_fallback_url(self):
        from src.config import Config

        cfg = Config.from_environment()
        assert cfg.webhook_url == "http://app/queue/webhook"

    @patch.dict("os.environ", {}, clear=True)
    def test_validate_fails_no_url(self):
        from src.config import Config

        cfg = Config.from_environment()
        valid, err = cfg.validate()
        assert valid is False
        assert "APP_WEBHOOK_URL" in err
