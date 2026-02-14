# Lambda-Webhook

Freshdesk webhook processor for Loopper-AI. Receives webhooks from Freshdesk, parses and formats ticket data, and queues to SQS FIFO for downstream processing.

## Architecture

```
Freshdesk Webhook → API Gateway → Lambda → SQS FIFO → EventBridge Pipes → App Layer
```

## Features

- **Industry Standard Webhook Processing**: Returns 200 after queueing to prevent Freshdesk retries
- **No Tickets Lost**: FIFO queue with deduplication ensures reliable delivery
- **Smart Enrichment**: Fetches full conversation history for updated events via Freshdesk API
- **Raw Fallback**: Unparseable webhooks queued with `_raw: true` flag for app-layer retry
- **Stateless App Layer**: Lambda handles all Freshdesk API calls, app-layer receives complete data

## Project Structure

```
Lambda-Webhook/
├── src/
│   ├── handler.py              # Main Lambda handler
│   ├── config.py               # Configuration management
│   ├── models.py               # Data models and types
│   ├── clients/
│   │   ├── freshdesk_client.py # Freshdesk API client
│   │   └── secrets_client.py   # AWS Secrets Manager client
│   ├── parsers/
│   │   ├── webhook_parser.py   # Webhook payload parser
│   │   └── ticket_parser.py    # Ticket data parser
│   ├── services/
│   │   └── sqs_service.py      # SQS FIFO service
│   └── utils/
│       ├── html_utils.py       # HTML cleaning utilities
│       └── response_utils.py   # HTTP response builders
├── requirements.txt
├── template.yaml               # SAM deployment template
└── README.md
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QUEUE_URL` | Yes | SQS FIFO queue URL |
| `SECRETS_ARN` | No | ARN of secret containing Freshdesk credentials |
| `PROCESS_CREATED` | No | Process "created" events (default: true) |
| `PROCESS_UPDATED` | No | Process "updated" events (default: true) |
| `API_TIMEOUT` | No | Freshdesk API timeout in seconds (default: 30.0) |
| `LOG_LEVEL` | No | Logging level (default: INFO) |

## Secrets Manager Format

The secret referenced by `SECRETS_ARN` should contain:

```json
{
  "FRESHDESK_BASE_URL": "https://your-domain.freshdesk.com",
  "FRESHDESK_API_KEY": "your-api-key"
}
```

## Event Flow

### Created Events
1. Receive webhook from Freshdesk
2. Extract ticket data from webhook payload
3. Build agent input with initial message
4. Queue to SQS with deduplication

### Updated Events
1. Receive webhook from Freshdesk
2. Extract ticket ID
3. Fetch full ticket data (including conversations) from Freshdesk API
4. Parse and build complete agent input
5. Queue to SQS with deduplication

### Parse Failures
1. Queue raw payload with `_raw: true` flag
2. Include parse error message
3. App-layer can retry parsing or handle manually

## Message Format

### Formatted Message
```json
{
  "event_type": "created|updated",
  "ticket_id": "123",
  "ticket": {
    "ticket_id": "123",
    "messages": [...],
    "message_count": 5,
    "started_at": "2025-01-01T00:00:00Z",
    "last_updated_at": "2025-01-01T01:00:00Z",
    "duration_hours": 0.0,
    "languages": [],
    "incoming_count": 3,
    "outgoing_count": 2,
    "conversation_flow": ["incoming", "outgoing", ...]
  }
}
```

### Raw Fallback Message
```json
{
  "_raw": true,
  "_parse_error": "Error description",
  "payload": {...},
  "event_type": "created|updated|unknown",
  "ticket_id": "123"
}
```

## SQS FIFO Configuration

- **MessageGroupId**: Set to `ticket_id` for ordering per ticket
- **MessageDeduplicationId**: SHA-256 hash of payload + request ID (5-minute deduplication window)

## Deployment

### Using SAM CLI

```bash
# Build
sam build

# Deploy
sam deploy --guided

# Or with parameters
sam deploy \
  --parameter-overrides \
    QueueUrl=https://sqs.us-east-1.amazonaws.com/123456789012/my-queue.fifo \
    SecretsArn=arn:aws:secretsmanager:us-east-1:123456789012:secret:freshdesk-creds
```

### Using AWS CLI

```bash
# Package code
zip -r lambda-webhook.zip src/

# Update function
aws lambda update-function-code \
  --function-name lambda-webhook \
  --zip-file fileb://lambda-webhook.zip

# Update environment variables
aws lambda update-function-configuration \
  --function-name lambda-webhook \
  --environment Variables="{QUEUE_URL=https://...,SECRETS_ARN=arn:...}"
```

## Development

### Local Testing

```python
from src.handler import lambda_handler

event = {
    "body": '{"freshdesk_webhook": {...}}',
    "isBase64Encoded": False,
    "requestContext": {"requestId": "test-123"}
}

response = lambda_handler(event, None)
print(response)
```

### Type Checking

```bash
pip install mypy
mypy src/
```

## Monitoring

Key CloudWatch metrics to monitor:
- Lambda invocations
- Lambda errors
- Lambda duration
- SQS messages sent
- SQS send failures

## Error Handling

1. **Configuration errors**: Returns 500, Lambda logs error
2. **Invalid JSON**: Returns 400, rejects request
3. **Parse failures**: Returns 200, queues raw message
4. **SQS failures**: Returns 500 after raw fallback also fails
5. **Freshdesk API failures**: Falls back to raw message

## Best Practices

- **Return 200 quickly**: Prevents Freshdesk from retrying
- **Deduplication**: Prevents duplicate processing within 5-minute window
- **Raw fallback**: Ensures no tickets are lost
- **Logging**: Structured logs for debugging and monitoring
- **Secrets rotation**: Use AWS Secrets Manager for credentials
- **Timeout**: Set Lambda timeout > API timeout to handle slow Freshdesk responses

## License

Copyright 2025 Loopper-AI
