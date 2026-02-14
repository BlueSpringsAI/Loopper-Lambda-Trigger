# Lambda-SQS

SQS-to-app-server forwarder for Loopper-AI. Consumes messages from an SQS FIFO queue (placed there by Lambda-Webhook) and POSTs them to the app server at `/queue/webhook`.

## Architecture

```
SQS FIFO Queue
     │  (event source mapping)
     ▼
  Lambda-SQS
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

```
Lambda-SQS/
├── src/
│   ├── handler.py             # Lambda entry point
│   ├── config.py              # Configuration management
│   ├── models.py              # Data models (SQSRecord, ForwardResult, BatchItemFailure)
│   ├── clients/
│   │   └── http_client.py     # HTTP POST client for app server
│   ├── parsers/
│   │   └── sqs_event_parser.py # SQS event extraction & payload builder
│   ├── services/
│   │   └── forwarder.py       # Orchestrates forwarding + batch failure response
│   └── utils/
│       └── logging_utils.py   # Logging setup & request ID extraction
├── tests/
│   └── test_handler.py        # Unit tests
├── events/                    # SAM local invoke test events
│   ├── test-sqs-single.json
│   ├── test-sqs-batch.json
│   └── test-sqs-raw-fallback.json
├── template.yaml              # SAM deployment template
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
| `REQUEST_TIMEOUT` | No | `15` | HTTP timeout in seconds |
| `LOG_LEVEL` | No | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_BODY_TRUNCATE` | No | `500` | Max bytes of body to log (avoids PII in logs) |

*One of `APP_WEBHOOK_URL` or `APP_SERVER_URL` is required.

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

### SAM CLI

```bash
cd Loopper-Lambda-Trigger/Lambda-SQS

# Build & deploy
sam build
sam deploy --guided

# Provide parameters:
#   AppWebhookUrl: http://<ALB>/queue/webhook
#   QueueArn: arn:aws:sqs:us-east-1:123456789012:webhook-queue.fifo
```

### Manual

```bash
# Package
cd src && zip -r ../lambda-sqs.zip . && cd ..

# Deploy
aws lambda update-function-code \
  --function-name loopper-lambda-sqs \
  --zip-file fileb://lambda-sqs.zip
```

## Testing

```bash
# Install dev dependencies
make install

# Run tests
make test

# Local invoke with SAM
make local-invoke
```

## Key Design Decisions

- **Zero external dependencies**: Uses only Python stdlib (`urllib`, `json`, `ssl`). No `requests` library needed — keeps cold starts minimal.
- **ReportBatchItemFailures**: Only failed messages retry; successful ones are deleted. Standard AWS best practice for SQS Lambda triggers.
- **Full event forwarding**: Sends the entire SQS event shape to the app server (not just the body), so the app can access message attributes and metadata.
- **Immutable config**: `Config` is a frozen dataclass — no accidental mutation during processing.

## License

Copyright 2025 Loopper-AI
