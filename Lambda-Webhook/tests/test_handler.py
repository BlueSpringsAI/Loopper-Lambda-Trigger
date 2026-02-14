# Copyright 2025 Loopper-AI
# Tests for Lambda handler

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestLambdaHandler:
    """Test suite for lambda_handler function."""

    @pytest.fixture
    def mock_config(self):
        """Mock configuration."""
        with patch("src.handler.Config") as mock:
            config = MagicMock()
            config.queue_url = "https://sqs.us-east-1.amazonaws.com/123/test.fifo"
            config.secrets_arn = "arn:aws:secretsmanager:us-east-1:123:secret:test"
            config.validate.return_value = (True, None)
            config.should_process_event.return_value = True
            mock.from_environment.return_value = config
            yield mock

    @pytest.fixture
    def created_event(self):
        """Sample created webhook event."""
        return {
            "body": json.dumps({
                "freshdesk_webhook": {
                    "ticket_id": "123",
                    "ticket_subject": "Test ticket",
                    "ticket_description": "Test description",
                    "ticket_contact_email": "customer@example.com",
                    "triggered_event": "ticket_created",
                }
            }),
            "isBase64Encoded": False,
            "requestContext": {"requestId": "test-request-123"},
        }

    @patch("src.handler.SQSService")
    def test_created_webhook_success(self, mock_sqs, mock_config, created_event):
        """Test successful processing of created webhook."""
        from src.handler import lambda_handler

        # Mock SQS send
        mock_sqs_instance = MagicMock()
        mock_sqs_instance.send_message.return_value = "msg-123"
        mock_sqs_instance.generate_deduplication_id.return_value = "dedup-123"
        mock_sqs.return_value = mock_sqs_instance

        # Invoke handler
        response = lambda_handler(created_event, None)

        # Assertions
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "accepted"
        assert body["ticket_id"] == "123"
        assert "messageId" in body

    def test_invalid_json(self, mock_config):
        """Test handling of invalid JSON payload."""
        from src.handler import lambda_handler

        event = {
            "body": "invalid json",
            "isBase64Encoded": False,
            "requestContext": {"requestId": "test-123"},
        }

        response = lambda_handler(event, None)

        assert response["statusCode"] == 400
        body = json.loads(response["body"])
        assert body["status"] == "rejected"

    def test_missing_queue_url(self):
        """Test error when QUEUE_URL is not configured."""
        from src.handler import lambda_handler

        with patch("src.handler.Config") as mock:
            config = MagicMock()
            config.validate.return_value = (False, "QUEUE_URL not configured")
            mock.from_environment.return_value = config

            event = {
                "body": "{}",
                "isBase64Encoded": False,
                "requestContext": {"requestId": "test-123"},
            }

            response = lambda_handler(event, None)

            assert response["statusCode"] == 500
            body = json.loads(response["body"])
            assert "QUEUE_URL" in body["message"]
