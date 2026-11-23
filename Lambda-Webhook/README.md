# Lambda-Webhook

Freshdesk webhook receiver for Loopper-AI, deployed as the `loopper-ai-<env>-api-to-sqs` Lambda. Receives webhooks from Freshdesk via API Gateway and queues them to SQS FIFO **as-is** for the app server to process. It does not call the Freshdesk API: the app server fetches the ticket itself and is the only Freshdesk caller, so both share one rate-limit budget.

## Architecture

```
Freshdesk Webhook → API Gateway → api-to-sqs (this Lambda) → SQS FIFO → pipe-to-app-server (Lambda-SQS) → App Server
```

## Features

- **Industry Standard Webhook Processing**: Returns 200 as soon as the message is queued
- **Forward as-is**: No Freshdesk API calls; the webhook payload is wrapped in a small envelope and queued
- **Per-ticket ordering**: FIFO `MessageGroupId` is the ticket ID
- **Stays under the SQS size limit**: Oversized webhooks have their large payload fields dropped instead of failing `SendMessage`
- **No external dependencies**: Python stdlib plus `boto3` (provided by the Lambda Python runtime)

## Project Structure

The deployed code is the single root-level `handler.py`. CI zips only that file (see [Deployment](#deployment)). The `src/` package and the SAM/Docker files are an older modular version that is **not deployed** and no longer matches the live behaviour (it still fetches tickets from Freshdesk).

```
Lambda-Webhook/
├── handler.py                  # Deployed Lambda code (entry point: handler.lambda_handler)
├── tests/
│   ├── test_sqs_message_size.py  # Tests for the root handler.py (SQS size limit)
│   └── test_handler.py         # Tests for the legacy src/ package
├── events/                     # Sample API Gateway events (`make local-invoke` uses test-created.json)
│   ├── test-created.json
│   └── test-updated.json
├── src/                        # Legacy modular version (not deployed)
│   ├── handler.py
│   ├── config.py
│   ├── models.py
│   ├── clients/                # freshdesk_client.py, secrets_client.py
│   ├── parsers/                # webhook_parser.py, ticket_parser.py
│   ├── services/               # sqs_service.py
│   └── utils/                  # html_utils.py, response_utils.py
├── template.yaml               # Legacy SAM template (builds src/, not used by CI)
├── Dockerfile                  # Legacy container image (copies src/, not used by CI)
├── Makefile                    # Install/test/lint/SAM helpers (build, lint and package target src/)
├── ARCHITECTURE.md             # Describes the legacy src/ design
├── DEPLOYMENT.md               # Describes the legacy SAM deployment
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── pytest.ini
└── README.md
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QUEUE_URL` | Yes | SQS FIFO queue URL |
| `PROCESS_CREATED` | No | Process "created" events (default: true) |
| `PROCESS_UPDATED` | No | Process "updated" events (default: true) |

`PROCESS_CREATED` / `PROCESS_UPDATED` count as enabled when set to `1`, `true` or `yes` (case-insensitive); any other value skips that event type. Events of unknown type are always queued.

The deployed handler does not read `SECRETS_ARN`, `API_TIMEOUT` or `LOG_LEVEL` (those belong to the legacy `src/` package); it logs at INFO.

## Event Flow

1. Receive webhook from Freshdesk (API Gateway event; base64 bodies are decoded)
2. Parse the JSON body; reject with 400 if it is not a JSON object
3. Read the ticket ID and event type from the `freshdesk_webhook` block (or the top-level payload if there is no such block)
4. If the event type is disabled via `PROCESS_CREATED` / `PROCESS_UPDATED`, return 200 `skipped`
5. Wrap the payload in the message envelope (below) and queue to SQS with deduplication
6. Return 200 `accepted` with the SQS message ID

### Event Type Detection

The `triggered_event` field is matched case-insensitively:

| `triggered_event` contains | `event_type` |
|----------------------------|--------------|
| `created` | `created` |
| `update` | `updated` |
| anything else / missing | `unknown` |

The ticket ID is `ticket_id`, else `id`, else `"unknown"`.

## Message Format

Every message has the same envelope. `_raw` is always `true`; the app server reads `event_type` / `ticket_id` and fetches the ticket from Freshdesk itself (if Freshdesk is rate limited, processing fails and SQS redelivers).

```json
{
  "_raw": true,
  "payload": {"freshdesk_webhook": {...}},
  "event_type": "created|updated|unknown",
  "ticket_id": "123"
}
```

### Oversized Webhooks

SQS rejects bodies of 256 KB or more. If the JSON message is larger than 250 KB:

1. Payload fields whose JSON value is larger than 2 KB are dropped (from the `freshdesk_webhook` block if present, otherwise from the top-level payload) and `"payload_trimmed": true` is added. The dropped field names are logged as a warning.
2. If the message is still over 250 KB, `payload` is replaced with `{}` (still with `"payload_trimmed": true`).

```json
{
  "_raw": true,
  "payload": {"freshdesk_webhook": {"ticket_id": "123", "triggered_event": "ticket_updated", "...": "small fields only"}},
  "event_type": "updated",
  "ticket_id": "123",
  "payload_trimmed": true
}
```

Webhooks under the limit are forwarded unchanged.

## SQS FIFO Configuration

- **MessageGroupId**: Set to `ticket_id` for ordering per ticket
- **MessageDeduplicationId**: SHA-256 hash of the raw request body + API Gateway request ID (5-minute deduplication window)

## Deployment

Deployed by GitHub Actions, not SAM. On every push that changes `Lambda-Webhook/handler.py` (or `Lambda-SQS/handler.py` / `Lambda-S3-RAG/handler.py`), and on manual `workflow_dispatch`, CI zips this folder's `handler.py` and updates the function code:

| Branch | Workflow | Function |
|--------|----------|----------|
| `dev` | `.github/workflows/lambdas-dev-deploy.yaml` | `loopper-ai-dev-api-to-sqs` |
| `main` | `.github/workflows/lambdas-actions.yaml` | `loopper-ai-staging-api-to-sqs` |

Region: `eu-central-1`. CI only updates the code; environment variables and the API Gateway trigger are configured on the function outside this repo.

### Using AWS CLI (same steps as CI)

```bash
# Package code (root handler.py only)
cd Lambda-Webhook
zip -q ../api-to-sqs.zip handler.py

# Update function
aws lambda update-function-code \
  --function-name loopper-ai-<env>-api-to-sqs \
  --zip-file fileb://../api-to-sqs.zip \
  --region eu-central-1
```

## Development

### Local Testing

The root handler creates its SQS client at import time, so an AWS region must be set (the test sets `AWS_DEFAULT_REGION`). `tests/test_sqs_message_size.py` shows how to load the root `handler.py` and mock the SQS client:

```bash
make install
python -m pytest tests/test_sqs_message_size.py
```

`tests/test_handler.py`, `make lint`, `make package`, `make build`/`deploy` and `make local-invoke` all target the legacy `src/` package or SAM stack, not the deployed handler.

## Monitoring

Key CloudWatch metrics to monitor:
- Lambda invocations
- Lambda errors
- Lambda duration
- SQS messages sent

Useful log lines:
- `event_type=<type> ticket_id=<id>`: every accepted request
- `Webhook <n> bytes over SQS limit — dropped large payload fields [...]`: payload was trimmed
- `SQS SendMessage failed` / `SQS send failed ticket_id=<id>`: message was not queued

## Error Handling

| Case | Response |
|------|----------|
| `QUEUE_URL` not set | 500 `{"status": "error", "message": "QUEUE_URL not configured"}` |
| Body is not a JSON object | 400 `{"status": "rejected", "message": "Invalid JSON body"}` |
| Event type disabled | 200 `{"status": "skipped", ...}` |
| Queued | 200 `{"status": "accepted", "ticket_id": ..., "messageId": ...}` |
| SQS `SendMessage` fails | 500 `{"status": "error", "ticket_id": ..., "message": "Failed to queue message"}` |

There is no fallback path: a failed send returns 500.

## Best Practices

- **Return 200 quickly**: No Freshdesk calls in the request path
- **Deduplication**: Prevents duplicate processing within 5-minute window
- **Keep messages small**: Large fields are trimmed so oversized tickets still reach the app server
- **Logging**: Ticket ID and event type logged for every request

## License

Copyright 2025 Loopper-AI
