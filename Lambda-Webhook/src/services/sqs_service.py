# Copyright 2025 Loopper-AI
# SQS FIFO queue service

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class SQSService:
    """SQS FIFO queue operations."""

    def __init__(self, queue_url: str):
        self.queue_url = queue_url
        self._client = boto3.client("sqs")

    def send_message(
        self,
        message_body: dict[str, Any],
        message_group_id: str,
        deduplication_id: str,
    ) -> str | None:
        """Send message to SQS FIFO. Returns MessageId or None on failure."""
        try:
            response = self._client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(message_body, default=str),
                MessageGroupId=message_group_id or "default",
                MessageDeduplicationId=deduplication_id[:128],
            )
            msg_id = response.get("MessageId")
            logger.info("SQS sent: message_id=%s group=%s", msg_id, message_group_id)
            return msg_id

        except ClientError as e:
            logger.error("SQS error [%s]: %s", e.response.get("Error", {}).get("Code"), e)
            return None
        except Exception as e:
            logger.exception("SQS unexpected error: %s", e)
            return None
