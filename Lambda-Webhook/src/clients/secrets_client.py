# Copyright 2025 Loopper-AI
# AWS Secrets Manager client

from __future__ import annotations

import json
import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ..models import FreshdeskCredentials

logger = logging.getLogger(__name__)


class SecretsClient:
    """Client for AWS Secrets Manager operations."""

    def __init__(self):
        """Initialize Secrets Manager client."""
        self._client = boto3.client("secretsmanager")

    def get_freshdesk_credentials(self, secret_arn: str) -> FreshdeskCredentials | None:
        """
        Retrieve Freshdesk credentials from Secrets Manager.

        Args:
            secret_arn: ARN of the secret containing Freshdesk credentials

        Returns:
            FreshdeskCredentials object or None if retrieval fails
        """
        try:
            response = self._client.get_secret_value(SecretId=secret_arn)
            secret_string = response.get("SecretString", "{}")
            data: dict[str, Any] = json.loads(secret_string)

            base_url = (data.get("FRESHDESK_BASE_URL") or "").strip().rstrip("/")
            api_key = (data.get("FRESHDESK_API_KEY") or "").strip()

            if not base_url or not api_key:
                logger.error("Missing Freshdesk credentials in secret")
                return None

            return FreshdeskCredentials(base_url=base_url, api_key=api_key)

        except ClientError as e:
            logger.error("Failed to retrieve secret %s: %s", secret_arn, e)
            return None
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in secret %s: %s", secret_arn, e)
            return None
        except Exception as e:
            logger.exception("Unexpected error retrieving secret: %s", e)
            return None
