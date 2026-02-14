# Copyright 2025 Loopper-AI
# HTTP client for forwarding requests to app server

from __future__ import annotations

import logging
import ssl
import urllib.error
import urllib.request

from ..models import ForwardResult

logger = logging.getLogger(__name__)


class HttpClient:
    """HTTP client for posting JSON payloads to the app server."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout
        self._ssl_ctx = ssl.create_default_context()

    def post_json(self, url: str, payload: bytes) -> ForwardResult:
        """POST JSON payload. Returns ForwardResult.

        Note: urllib.request.urlopen raises HTTPError for non-2xx status codes,
        so the success path inside the try block is always 2xx.
        """
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_ctx) as resp:
                code = resp.getcode()
                body = resp.read().decode("utf-8", errors="replace")
                return ForwardResult(success=True, status_code=code, response_body=body)

        except urllib.error.HTTPError as exc:
            err_body = exc.read()[:500].decode("utf-8", errors="replace")
            logger.error("HTTPError: code=%s body=%s", exc.code, err_body)
            return ForwardResult(success=False, status_code=exc.code, error=f"HTTPError {exc.code}")

        except urllib.error.URLError as exc:
            logger.error("URLError: reason=%s", exc.reason)
            return ForwardResult(success=False, error=f"URLError: {exc.reason}")

        except Exception as exc:
            logger.exception("Unexpected HTTP error: %s", exc)
            return ForwardResult(success=False, error=str(exc))
