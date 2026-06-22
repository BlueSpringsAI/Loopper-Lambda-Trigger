"""AWS Lambda handler for S3-triggered RAG ingestion.

Processes S3 events to automatically ingest documents into the RAG service:
- ObjectCreated: Downloads file from S3, POSTs to /api/v1/ingest/file
- ObjectRemoved: Calls DELETE /api/v1/sources to remove chunks from Qdrant

Collection mapping: First path segment of the S3 key = collection name.
  s3://bucket/ecommerce/faq.pdf  -> collection "ecommerce"
  s3://bucket/policies/doc.md    -> collection "policies"
  s3://bucket/file.pdf           -> default collection (None)
"""

import json
import logging
import os
from urllib.parse import unquote_plus

import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:8000")
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "600"))
MAX_FILE_SIZE_MB = int(os.environ.get("MAX_FILE_SIZE_MB", "50"))

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt",
    ".xlsx", ".xls", ".html", ".htm",
    ".md", ".markdown", ".txt", ".json",
}

s3_client = boto3.client("s3")


def handler(event, context):
    """Lambda entry point. Processes S3 event records."""
    logger.info("Received event with %d record(s)", len(event.get("Records", [])))

    for record in event.get("Records", []):
        event_name = record.get("eventName", "")
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        logger.info("Processing event=%s bucket=%s key=%s", event_name, bucket, key)

        if event_name.startswith("ObjectCreated"):
            _handle_created(bucket, key, record)
        elif event_name.startswith("ObjectRemoved"):
            _handle_removed(bucket, key, record)
        else:
            logger.warning("Ignoring unsupported event: %s", event_name)

    return {"statusCode": 200, "body": "OK"}


def _parse_collection_and_filename(key):
    """Extract collection name and filename from S3 key.

    Convention: first path segment = collection name.
    If key has no directory prefix, collection is None (uses RAG default).

    Args:
        key: S3 object key, e.g. "ecommerce/docs/faq.pdf" or "file.pdf"

    Returns:
        Tuple of (collection, filename).
        collection is None if the key has no prefix directory.
    """
    parts = key.split("/")
    filename = parts[-1]

    if len(parts) >= 2:
        collection = parts[0]
    else:
        collection = None

    return collection, filename


def _handle_created(bucket, key, record):
    """Download file from S3 and POST to RAG ingest endpoint."""
    collection, filename = _parse_collection_and_filename(key)

    # Validate file extension
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        logger.info("Skipping unsupported extension: %s (file: %s)", ext, filename)
        return

    # Check file size from event metadata
    file_size = record["s3"]["object"].get("size", 0)
    max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        logger.warning(
            "Skipping oversized file: %s (%.1f MB > %d MB limit)",
            filename, file_size / (1024 * 1024), MAX_FILE_SIZE_MB,
        )
        return

    # Download file from S3
    logger.info("Downloading s3://%s/%s (%.1f MB)", bucket, key, file_size / (1024 * 1024))
    response = s3_client.get_object(Bucket=bucket, Key=key)
    file_bytes = response["Body"].read()

    # Build multipart form data
    url = f"{RAG_SERVICE_URL.rstrip('/')}/api/v1/ingest/file"
    files = {"file": (filename, file_bytes)}
    data = {}

    if collection:
        data["collection"] = collection

    metadata = json.dumps({"source": filename, "s3_bucket": bucket, "s3_key": key})
    data["metadata"] = metadata

    logger.info("POSTing to %s (collection=%s, file=%s)", url, collection, filename)
    resp = requests.post(url, files=files, data=data, timeout=REQUEST_TIMEOUT)

    if resp.status_code == 400:
        # Client error — don't retry (bad file format, empty file, etc.)
        logger.error("RAG service rejected file (400): %s — %s", filename, resp.text)
        return

    # For 5xx or connection errors, raise to trigger Lambda retry
    resp.raise_for_status()
    logger.info("Ingestion successful: %s — %s", filename, resp.json())


def _handle_removed(bucket, key, record):
    """Call RAG DELETE endpoint to remove chunks for the deleted file."""
    collection, filename = _parse_collection_and_filename(key)

    url = f"{RAG_SERVICE_URL.rstrip('/')}/api/v1/sources"
    params = {"source": filename}
    if collection:
        params["collection"] = collection

    logger.info("DELETE %s params=%s", url, params)
    resp = requests.delete(url, params=params, timeout=REQUEST_TIMEOUT)

    if resp.status_code == 404:
        # Source already gone — nothing to do
        logger.info("Source already deleted (404): %s", filename)
        return

    if resp.status_code == 400:
        logger.error("RAG service rejected delete (400): %s — %s", filename, resp.text)
        return

    resp.raise_for_status()
    logger.info("Deletion successful: %s — %s", filename, resp.json())
