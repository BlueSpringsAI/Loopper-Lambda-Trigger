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
   - On **failure**: all messages are reported as failed, and each one is hidden for an increasing delay (90 s, 120 s, 150 s …) before SQS redelivers it, until the queue moves it to the DLQ. See [Retries and Back-off](#retries-and-back-off).

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
│   ├── test_retry_delay.py    # Tests for the deployed root handler.py (retry back-off)
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
| `RETRY_BASE_S` | `90` | Retry delay in seconds after the first failed delivery |
| `RETRY_STEP_S` | `30` | Seconds added to the delay for each further failed delivery |
| `RETRY_MAX_S` | `900` | Upper limit on the retry delay in seconds |

There are no environment variables for the retry back-off. To change it, edit these constants.

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
// Lambda reports all records as failed → back-off → SQS retries → DLQ
{
  "batchItemFailures": [
    {"itemIdentifier": "msg-001"},
    {"itemIdentifier": "msg-002"}
  ]
}
```

Before returning, the handler sets each failed message's next retry delay (see below).

## Retries and Back-off

When the POST fails, the handler calls SQS `ChangeMessageVisibility` on each failed message so it reappears after an increasing delay. Without this, every retry would wait the queue's own fixed visibility timeout.

```
delay = min(RETRY_BASE_S + RETRY_STEP_S × (receive_count − 1), RETRY_MAX_S)
      = min(90 + 30 × (receive_count − 1), 900) seconds
```

`receive_count` is the record's `ApproximateReceiveCount` attribute, so the delay grows with every failed delivery:

| Failed delivery | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|-----------------|---|---|---|---|---|---|---|---|---|----|
| Next retry in (s) | 90 | 120 | 150 | 180 | 210 | 240 | 270 | 300 | 330 | 360 |

The 900 s cap is reached from the 28th failed delivery onwards. The code comments assume the queue's `maxReceiveCount` is 10. On that setting, the 10th try comes about 31.5 minutes (1,890 s) after the first. If that try also fails, SQS moves the message to the DLQ instead of delivering it again. The queue's `maxReceiveCount`, visibility timeout and DLQ are configured outside this repo.

How it works:

- The queue URL and region are derived from each record's `eventSourceARN`.
- `boto3` is imported only when there is a failure. The handler creates one SQS client per region, with a 2 s connect timeout, a 3 s read timeout and no SDK retries.
- Each delay is logged at INFO: `retry in <n>s (receive <count>) message_id=<id>`.
- **Best effort**: if the call fails (for example `AccessDenied` because the permission below is missing), or `boto3` cannot be imported, the handler logs `could not set retry delay for message_id=<id>: <error>`. The message still counts as failed and comes back after the queue's own visibility timeout.
- If neither `APP_WEBHOOK_URL` nor `APP_SERVER_URL` is set, the handler raises before the POST, so there is no back-off. The whole batch is retried on the queue's visibility timeout.

### Required IAM permission

The function's execution role needs **`sqs:ChangeMessageVisibility`** on the source queue. This is in addition to the receive, delete and get-attributes permissions the SQS trigger already needs. Without it, the back-off falls back to the queue's fixed timeout and nothing fails. The only sign is the `could not set retry delay` warning in the logs. IAM is managed outside this repo; CI does not change it.

## Deployment

> **⚠️ Do not use `make deploy`, `make deploy-guided` or `sam deploy`.** They `sam build` the legacy `template.yaml`, which runs the old `src/` package (`Handler: src.handler.lambda_handler`). They then deploy it as a separate SAM/CloudFormation stack with the old names: the function is `loopper-lambda-sqs`, and `make logs` assumes stack `loopper-lambda-sqs`. That is not the live `loopper-ai-<env>-pipe-to-app-server` function, and the code it deploys has no retry back-off. **CI is the only deploy path.**

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

`tests/test_retry_delay.py` tests the deployed root `handler.py`. It loads the file directly, mocks `urlopen` and `boto3`, and covers:

- the delay schedule (90, 120 … 360 s for failures 1–10, and the 900 s cap)
- parsing the queue URL and region from the ARN
- a failed POST calling `change_message_visibility` with the right delay
- a successful POST changing nothing
- an SQS error still returning `batchItemFailures`

```bash
cd Lambda-SQS
pip install -r requirements-dev.txt boto3
python -m pytest tests/test_retry_delay.py
```

`boto3` must be installed locally. The test replaces `boto3` with a mock, but the handler still imports `botocore.config` for real, and `requirements-dev.txt` includes neither package. Without it, the back-off is skipped and `test_failed_message_gets_its_next_delay` fails. `pytest.ini` adds `--cov=src`, so the coverage report covers the legacy package, not the root `handler.py`.

`tests/test_handler.py` targets the legacy `src/` package. `make test` runs both files, and `make local-invoke` runs the legacy SAM template.

## Key Design Decisions

- **No packaged dependencies**: The POST uses only the Python stdlib (`urllib`, `json`, `ssl`); there is no `requests` library. `boto3` is used only for the retry back-off. It comes with the Lambda Python runtime, so nothing is bundled, and it is imported only when a delivery fails.
- **Increasing retry delay**: Failed messages back off 90, 120, 150 … s instead of retrying on a fixed timeout. This spreads the tries across a Freshdesk rate-limit peak before a message reaches the DLQ.
- **ReportBatchItemFailures**: Only failed messages retry; successful ones are deleted. Standard AWS best practice for SQS Lambda triggers.
- **Full event forwarding**: Sends the entire SQS event shape to the app server (not just the body), so the app can access message attributes and metadata.

## License

Copyright 2025 Loopper-AI
