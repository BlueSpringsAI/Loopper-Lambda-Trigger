"""AWS Lambda handler for S3-triggered RAG ingestion.

Processes S3 events to automatically ingest documents into the RAG service:
- ObjectCreated: Downloads file from S3, POSTs to /api/v1/ingest/file
- ObjectRemoved: Calls DELETE /api/v1/sources to remove chunks from Qdrant

Key layout (written by the KB upload API / UI):
  <collection>/<filename>                   -> common document
  <collection>/groups/<team_key>/<filename> -> group document
  <filename>                                -> default collection, common

The uploader stamps doc_version as S3 object metadata; after a successful
ingest of a versioned document we call /api/v1/documents/activate so the
new version becomes the single active one.
"""

import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from urllib.parse import unquote_plus, urlencode

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

RAG_SERVICE_URL = os.environ.get("RAG_SERVICE_URL", "http://localhost:8001")
# The RAG API refuses callers without sign-in or this service key (Ticket-Inbox #388).
SERVICE_HEADERS = {"X-Internal-API-Key": os.environ.get("INTERNAL_API_KEY", "")}
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


def _parse_key(key):
    """Split an S3 key into (collection, team_key, filename).

    Convention: first path segment = collection name; a "groups/<team_key>/"
    segment after the collection marks a group-scoped document.
    If key has no directory prefix, collection is None (uses RAG default).

    Returns:
        Tuple of (collection, team_key, filename). team_key is None for
        common documents.
    """
    parts = key.split("/")
    filename = parts[-1]
    collection = parts[0] if len(parts) >= 2 else None
    team_key = None
    if len(parts) >= 4 and parts[1] == "groups" and parts[2]:
        team_key = parts[2]
    return collection, team_key, filename


def _build_multipart(fields, files):
    """Build a multipart/form-data body using only stdlib."""
    boundary = uuid.uuid4().hex
    body = b""
    for name, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += value.encode() + b"\r\n"
    for name, (filename, data) in files.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return boundary, body


def _handle_created(bucket, key, record):
    """Download file from S3 and POST to RAG ingest endpoint."""
    collection, team_key, filename = _parse_key(key)

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
    fields = {}
    if collection:
        fields["collection"] = collection
    # RAG service requires metadata.scope: 'common' or 'group'.
    # Scope/team come from the key path; S3 object metadata may override
    # and supplies doc_version (stamped by the KB upload API).
    s3_meta = response.get("Metadata", {}) or {}
    team_key = team_key or s3_meta.get("team_key") or s3_meta.get("team-key")
    metadata = {
        "source": filename,
        "s3_bucket": bucket,
        "s3_key": key,
        "scope": s3_meta.get("scope") or ("group" if team_key else "common"),
    }
    if team_key:
        metadata["team_key"] = team_key
    doc_version = s3_meta.get("doc_version") or s3_meta.get("doc-version")
    if doc_version:
        metadata["doc_version"] = doc_version
    fields["metadata"] = json.dumps(metadata)

    boundary, body = _build_multipart(fields, {"file": (filename, file_bytes)})
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **SERVICE_HEADERS},
        method="POST",
    )

    logger.info(
        "POSTing to %s (collection=%s, file=%s, scope=%s, team_key=%s, doc_version=%s)",
        url, collection, filename, metadata["scope"], team_key, doc_version,
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            resp_body = resp.read().decode()
            logger.info("Ingestion successful: %s — %s", filename, json.loads(resp_body))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        if e.code == 400:
            # Client error — don't retry (bad file format, empty file, etc.)
            logger.error("RAG service rejected file (400): %s — %s", filename, err_body)
            return
        raise  # 5xx — re-raise to trigger Lambda retry

    # A versioned upload replaces earlier versions: make it the active one.
    if doc_version:
        _activate_version(collection, filename, team_key, doc_version)


def _activate_version(collection, source, team_key, doc_version):
    """Mark doc_version as the active version; the service deactivates the rest."""
    payload = {"source": source, "doc_version": doc_version}
    if team_key:
        payload["team_key"] = team_key
    if collection:
        payload["collection"] = collection

    url = f"{RAG_SERVICE_URL.rstrip('/')}/api/v1/documents/activate"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **SERVICE_HEADERS},
        method="POST",
    )

    logger.info("Activating version: %s", payload)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            logger.info("Activation successful: %s — %s", source, resp.read().decode())
    except urllib.error.HTTPError as e:
        # Ingest already succeeded — log but never re-raise, or the Lambda
        # retry would ingest the same file again.
        logger.error(
            "Activation failed (%s): %s — %s",
            e.code, source, e.read().decode(errors="replace"),
        )


def _handle_removed(bucket, key, record):
    """Call RAG DELETE endpoint to remove chunks for the deleted file."""
    collection, team_key, filename = _parse_key(key)

    # Scope the delete to the layer the key belongs to, so a common file
    # and a group file sharing a filename never delete each other's chunks.
    params = {"source": filename, "layer": "group" if team_key else "common"}
    if team_key:
        params["team_key"] = team_key
    if collection:
        params["collection"] = collection

    url = f"{RAG_SERVICE_URL.rstrip('/')}/api/v1/sources?{urlencode(params)}"
    req = urllib.request.Request(url, headers=SERVICE_HEADERS, method="DELETE")

    logger.info("DELETE %s params=%s", url, params)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            resp_body = resp.read().decode()
            logger.info("Deletion successful: %s — %s", filename, json.loads(resp_body))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="replace")
        if e.code == 404:
            # Source already gone — nothing to do
            logger.info("Source already deleted (404): %s", filename)
            return
        if e.code == 400:
            logger.error("RAG service rejected delete (400): %s — %s", filename, err_body)
            return
        raise  # 5xx — re-raise to trigger Lambda retry
