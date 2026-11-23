# Lambda-SQS

SQS-to-app-server forwarder for Loopper-AI, deployed as the `loopper-ai-<env>-pipe-to-app-server` Lambda. Consumes messages from an SQS FIFO queue (placed there by Lambda-Webhook / `api-to-sqs`) and POSTs them to the app server at `/queue/webhook`.

## Architecture

```
SQS FIFO Queue
     │  (event source mapping)
     ▼
  Lambda-SQS (pipe-to-app-server)
     │  POST {"Records": [...]}
     ▼
  App Server (/queue/webhook)
     │
     └─→ 202 Accepted (background processing)
```

## How It Works

1. **SQS event source mapping** invokes this Lambda with `{"Records": [...]}`.
2. Lambda POSTs the full event as JSON to the app server.
3. App returns **202 Accepted** and processes in the background.
4. Lambda uses **ReportBatchItemFailures**:
   - On **2xx**: all messages are deleted from the queue.
   - On **failure**: all messages are reported as failed → SQS retries, then DLQ.

## Project Structure

The deployed code is the single root-level `handler.py`. CI zips only that file (see [Deployment](#deployment)). The `src/` package and the SAM/Docker files are an older modular version that is **not deployed**.

```
Lambda-SQS/
├── handler.py                 # Deployed Lambda code (entry point: handler.lambda_handler)
├── src/                       # Legacy modular version (not deployed)
│   ├── handler.py             # Lambda entry point
│   ├── config.py              # Configuration management
│   ├── models.py              # Data models (ForwardResult)
│   └── clients/
│       └── http_client.py     # HTTP POST client for app server
├── tests/
│   └── test_handler.py        # Unit tests for the legacy src/ package
├── events/                    # SAM local invoke test events
│   ├── test-sqs-single.json
│   ├── test-sqs-batch.json
│   └── test-sqs-raw-fallback.json
├── template.yaml              # Legacy SAM template (builds src/, not used by CI)
├── Dockerfile                 # Legacy container image (copies src/, not used by CI)
├── Makefile
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── pytest.ini
└── .gitignore
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APP_WEBHOOK_URL` | Yes* | — | Full URL of app webhook (e.g. `http://<ALB>/queue/webhook`) |
| `APP_SERVER_URL` | Yes* | — | Base URL fallback; `/queue/webhook` is appended |
| `LOG_LEVEL` | No | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

*One of `APP_WEBHOOK_URL` or `APP_SERVER_URL` is required. If neither is set the handler raises `ValueError`, so the whole batch fails and is retried.

These are constants in `handler.py`, not environment variables:

| Constant | Value | Description |
|----------|-------|-------------|
| `REQUEST_TIMEOUT` | `15` | HTTP timeout in seconds |
| `LOG_BODY_TRUNCATE` | `500` | Max bytes of the request body logged at `DEBUG` level |

## Request Format

```
Method:   POST
URL:      APP_WEBHOOK_URL (e.g. http://<ALB>/queue/webhook)
Headers:  Content-Type: application/json
Body:     {"Records": [{"messageId": "...", "body": "...", ...}]}
```

The app server extracts the Freshdesk payload from the first record's `body` field.

## Response Contract

### App returns 2xx (e.g. 202 Accepted)
```json
// Lambda returns parsed app response — no batch failures
{"status": "accepted"}
```

### App returns non-2xx or network error
```json
// Lambda reports all records as failed → SQS retries → DLQ
{
  "batchItemFailures": [
    {"itemIdentifier": "msg-001"},
    {"itemIdentifier": "msg-002"}
  ]
}
```

## Deployment

Deployed by GitHub Actions, not SAM. On every push that changes `Lambda-SQS/handler.py` (or `Lambda-Webhook/handler.py` / `Lambda-S3-RAG/handler.py`), and on manual `workflow_dispatch`, CI zips this folder's `handler.py` and updates the function code:

| Branch | Workflow | Function |
|--------|----------|----------|
| `dev` | `.github/workflows/lambdas-dev-deploy.yaml` | `loopper-ai-dev-pipe-to-app-server` |
| `main` | `.github/workflows/lambdas-actions.yaml` | `loopper-ai-staging-pipe-to-app-server` |

Region: `eu-central-1`. CI only updates the code; environment variables and the SQS trigger are configured on the function outside this repo.

### Manual (same steps as CI)

```bash
# Package (root handler.py only)
cd Loopper-Lambda-Trigger/Lambda-SQS
zip -q ../pipe-to-app-server.zip handler.py

# Deploy
aws lambda update-function-code \
  --function-name loopper-ai-<env>-pipe-to-app-server \
  --zip-file fileb://../pipe-to-app-server.zip \
  --region eu-central-1
```

## Testing

There are no tests for the deployed root `handler.py` yet. `make test` runs `tests/test_handler.py`, which targets the legacy `src/` package, and `make local-invoke` runs the legacy SAM template.

## Key Design Decisions

- **Zero external dependencies**: Uses only Python stdlib (`urllib`, `json`, `ssl`). No `requests` library needed — keeps cold starts minimal.
- **ReportBatchItemFailures**: Only failed messages retry; successful ones are deleted. Standard AWS best practice for SQS Lambda triggers.
- **Full event forwarding**: Sends the entire SQS event shape to the app server (not just the body), so the app can access message attributes and metadata.

## License

Copyright 2025 Loopper-AI
