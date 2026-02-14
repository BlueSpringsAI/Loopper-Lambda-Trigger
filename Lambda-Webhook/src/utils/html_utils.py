# Copyright 2025 Loopper-AI
# HTML processing utilities

from __future__ import annotations

import re


def clean_html_body(raw: str) -> str:
    """
    Strip HTML tags and collapse whitespace from text.

    This is a minimal HTML cleaning function for Lambda environment
    (no quotequail library dependency). Matches webhook_parsing.py intent.

    Args:
        raw: Raw HTML or text string

    Returns:
        Clean text with HTML tags removed and whitespace normalized
    """
    if not raw or not isinstance(raw, str):
        return ""

    # Strip HTML tags
    body = re.sub(r"<[^>]+>", " ", raw)

    # Collapse multiple spaces/tabs to single space
    body = re.sub(r"[ \t]+", " ", body)

    # Remove leading/trailing whitespace
    return body.strip()
