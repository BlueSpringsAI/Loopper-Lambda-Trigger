# Architecture - Lambda-Webhook

## System Overview

Lambda-Webhook is a serverless webhook processor that receives Freshdesk webhooks, parses ticket data, enriches it with full conversation history when needed, and queues messages to SQS FIFO for downstream processing.

```
┌─────────────┐
│  Freshdesk  │
│   Webhook   │
└──────┬──────┘
       │ HTTP POST
       ▼
┌─────────────────┐
│  API Gateway    │
│   (HTTP API)    │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│         Lambda-Webhook                  │
│  ┌───────────────────────────────────┐  │
│  │  Handler (handler.py)             │  │
│  │  - Parse API Gateway event        │  │
│  │  - Route to processors            │  │
│  │  - Return 200 quickly             │  │
│  └───────────────┬───────────────────┘  │
│                  │                       │
│  ┌───────────────▼───────────────────┐  │
│  │  Event Processor                  │  │
│  │  - Determine event type           │  │
│  │  - Extract ticket ID              │  │
│  │  - Check config filters           │  │
│  └───────────────┬───────────────────┘  │
│                  │                       │
│         ┌────────┴────────┐              │
│         ▼                 ▼              │
│  ┌─────────────┐   ┌─────────────────┐  │
│  │  Created    │   │   Updated       │  │
│  │  Parser     │   │   Enricher      │  │
│  │  (webhook)  │   │   (API fetch)   │  │
│  └──────┬──────┘   └────────┬────────┘  │
│         │                   │            │
│         │    ┌──────────────┘            │
│         │    │  Freshdesk API            │
│         │    │  (fetch conversations)    │
│         │    │                           │
│         ▼    ▼                           │
│  ┌─────────────────┐                    │
│  │  Agent Input    │                    │
│  │  Builder        │                    │
│  └────────┬────────┘                    │
│           │                             │
│           ▼                             │
│  ┌─────────────────┐                    │
│  │  SQS Service    │                    │
│  │  - Dedup ID     │                    │
│  │  - Group ID     │                    │
│  │  - Send message │                    │
│  └────────┬────────┘                    │
└───────────┼─────────────────────────────┘
            │
            ▼
    ┌──────────────┐
    │  SQS FIFO    │
    │  Queue       │
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │ EventBridge  │
    │   Pipes      │
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐
    │  App Layer   │
    └──────────────┘
```

## Module Architecture

### Core Modules

#### 1. Handler Module ([handler.py](src/handler.py))
**Responsibility**: Main entry point and orchestration

- `lambda_handler()`: AWS Lambda entry point
- `process_webhook()`: Orchestrates webhook processing
- `resolve_agent_input()`: Routes to appropriate parser
- `send_formatted_message()`: Queues parsed messages
- `send_raw_message()`: Fallback for parse failures

**Dependencies**: All other modules

#### 2. Configuration Module ([config.py](src/config.py))
**Responsibility**: Environment configuration management

- `Config`: Dataclass for application settings
- `from_environment()`: Loads from env vars
- `validate()`: Validates configuration
- `should_process_event()`: Event filtering logic

**Dependencies**: None (stdlib only)

#### 3. Models Module ([models.py](src/models.py))
**Responsibility**: Data structures and types

- `Message`: Single conversation message
- `Ticket`: Complete ticket with messages
- `AgentInput`: Formatted output for app-layer
- `RawMessage`: Fallback for failed parses
- `FreshdeskCredentials`: API credentials
- Type aliases: `EventType`, `MessageDirection`

**Dependencies**: None (stdlib only)

### Client Modules

#### 4. Freshdesk Client ([clients/freshdesk_client.py](src/clients/freshdesk_client.py))
**Responsibility**: Freshdesk API interaction

- `FreshdeskClient`: API client class
- `fetch_ticket_with_conversations()`: Fetch full ticket data
- Uses Basic Auth with API key
- Handles HTTP errors gracefully

**Dependencies**: `models.FreshdeskCredentials`, urllib, json

#### 5. Secrets Client ([clients/secrets_client.py](src/clients/secrets_client.py))
**Responsibility**: AWS Secrets Manager interaction

- `SecretsClient`: Secrets retrieval class
- `get_freshdesk_credentials()`: Fetch Freshdesk creds
- Error handling for missing/invalid secrets

**Dependencies**: `models.FreshdeskCredentials`, boto3

### Parser Modules

#### 6. Webhook Parser ([parsers/webhook_parser.py](src/parsers/webhook_parser.py))
**Responsibility**: Parse raw webhook payloads

- `get_event_type()`: Determine created/updated/unknown
- `extract_ticket_id()`: Extract ticket ID
- `build_agent_input_from_created()`: Parse created events
- Works with webhook payload directly

**Dependencies**: `models`, `utils.html_utils`

#### 7. Ticket Parser ([parsers/ticket_parser.py](src/parsers/ticket_parser.py))
**Responsibility**: Parse Freshdesk API responses

- `ticket_to_agent_input()`: Convert API response to agent input
- `_parse_conversation()`: Parse individual messages
- Handles conversation history
- Filters public vs private messages

**Dependencies**: `models`, `utils.html_utils`

### Service Modules

#### 8. SQS Service ([services/sqs_service.py](src/services/sqs_service.py))
**Responsibility**: Amazon SQS FIFO queue operations

- `SQSService`: Queue interaction class
- `send_message()`: Send with deduplication
- `generate_deduplication_id()`: SHA-256 hash generation
- Handles SQS client errors

**Dependencies**: boto3

### Utility Modules

#### 9. HTML Utils ([utils/html_utils.py](src/utils/html_utils.py))
**Responsibility**: HTML processing

- `clean_html_body()`: Strip tags, normalize whitespace
- Minimal dependency alternative to quotequail

**Dependencies**: re (stdlib)

#### 10. Response Utils ([utils/response_utils.py](src/utils/response_utils.py))
**Responsibility**: HTTP response formatting

- `create_response()`: Build API Gateway response
- Standardized JSON responses

**Dependencies**: json (stdlib)

## Data Flow

### Created Event Flow

```
1. Freshdesk → API Gateway → Lambda
2. Parse webhook payload (WebhookParser)
3. Extract ticket data from webhook
4. Build agent input with initial message
5. Generate deduplication ID (SHA-256)
6. Send to SQS FIFO (MessageGroupId=ticket_id)
7. Return 200 to Freshdesk
```

**Time**: ~100-200ms (no API call needed)

### Updated Event Flow

```
1. Freshdesk → API Gateway → Lambda
2. Parse webhook payload (WebhookParser)
3. Extract ticket ID
4. Fetch credentials from Secrets Manager
5. Call Freshdesk API for full ticket data
6. Parse ticket + conversations (TicketParser)
7. Build complete agent input
8. Generate deduplication ID
9. Send to SQS FIFO
10. Return 200 to Freshdesk
```

**Time**: ~500-1500ms (depends on Freshdesk API)

### Parse Failure Flow

```
1. Freshdesk → API Gateway → Lambda
2. Attempt to parse webhook
3. Parse fails (error caught)
4. Build raw message with _raw: true
5. Include parse error message
6. Generate unique deduplication ID
7. Send raw payload to SQS FIFO
8. Return 200 to Freshdesk (no ticket lost!)
```

**Time**: ~100-200ms

## Design Principles

### 1. Industry Standards

**FIFO Queue Pattern**
- `MessageGroupId`: ticket_id (ordering per ticket)
- `MessageDeduplicationId`: SHA-256 hash (5-min window)
- No duplicate processing
- Ordered message delivery per ticket

**Webhook Best Practices**
- Return 200 quickly (< 30s)
- Idempotent processing
- No retries from webhook source
- Async processing (queue + process later)

### 2. Reliability

**No Tickets Lost**
- Raw fallback on parse failure
- Queue before response
- Deduplication prevents duplicates
- FIFO ensures ordering

**Error Handling**
- Try formatted message first
- Fall back to raw on failure
- Log all errors
- Graceful degradation

### 3. Separation of Concerns

**Single Responsibility**
- Each module has one job
- Clear boundaries
- Testable in isolation
- Easy to maintain

**Dependency Management**
- Handler orchestrates
- Clients handle external services
- Parsers transform data
- Utils provide helpers
- Models define contracts

### 4. Stateless App Layer

**Lambda Enrichment**
- Lambda calls Freshdesk API
- Lambda holds API credentials
- App layer receives complete data
- App layer stays stateless

**Benefits**
- Single source of truth for API key
- No Freshdesk SDK in app layer
- Complete data in queue
- Simplified downstream processing

## Deployment Architecture

```
┌────────────────────────────────────────┐
│  CloudFormation Stack                  │
│  ┌──────────────────────────────────┐  │
│  │  Lambda Function                 │  │
│  │  - Python 3.12 runtime           │  │
│  │  - 512 MB memory                 │  │
│  │  - 60s timeout                   │  │
│  │  - Env vars from parameters      │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │  IAM Role                        │  │
│  │  - SQS SendMessage               │  │
│  │  - Secrets GetSecretValue        │  │
│  │  - CloudWatch Logs               │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │  HTTP API                        │  │
│  │  - POST /webhook endpoint        │  │
│  │  - Lambda integration            │  │
│  │  - 29s timeout                   │  │
│  └──────────────────────────────────┘  │
│  ┌──────────────────────────────────┐  │
│  │  CloudWatch Alarms               │  │
│  │  - Error rate > 5 in 5min        │  │
│  │  - Throttles > 0                 │  │
│  └──────────────────────────────────┘  │
└────────────────────────────────────────┘
```

## Security Architecture

### Credentials Management
- API keys in AWS Secrets Manager
- IAM role-based access
- No hardcoded secrets
- Automatic rotation support

### Network Security
- HTTPS only (API Gateway)
- Private Lambda execution
- VPC optional (not required)

### Access Control
- Least privilege IAM policies
- Function-specific role
- Resource-based policies

## Scalability

### Concurrency
- Auto-scaling Lambda
- Reserved concurrency (optional)
- SQS as buffer for spikes

### Performance
- Lightweight dependencies (boto3 only)
- Minimal cold start
- Efficient HTML parsing
- Connection reuse

### Limits
- API Gateway: 10,000 req/s (soft limit)
- Lambda: 1,000 concurrent (default)
- SQS FIFO: 3,000 msg/s per group

## Monitoring & Observability

### CloudWatch Metrics
- Invocations
- Errors
- Duration
- Throttles
- Concurrent executions

### CloudWatch Logs
- Structured logging
- Request IDs
- Error traces
- Performance metrics

### CloudWatch Alarms
- Error rate threshold
- Throttle detection
- Custom metrics (optional)

## Cost Optimization

### Lambda Costs
- Pay per invocation
- Pay per GB-second
- 512 MB typical usage
- ~$0.20 per million requests

### SQS Costs
- $0.50 per million requests
- FIFO pricing
- No data transfer costs (same region)

### Secrets Manager Costs
- $0.40 per secret per month
- $0.05 per 10,000 API calls

## Testing Strategy

### Unit Tests
- Mock external dependencies
- Test each module in isolation
- High code coverage

### Integration Tests
- Use moto for AWS mocking
- Test full webhook flow
- Verify SQS messages

### Local Testing
- SAM local invoke
- Test event files
- Environment variable override

## Future Enhancements

### Potential Improvements
1. **Batch Processing**: Group multiple messages
2. **Dead Letter Queue**: Handle persistent failures
3. **Metrics Dashboard**: Custom CloudWatch dashboard
4. **X-Ray Tracing**: Detailed performance analysis
5. **API Key Auth**: Secure webhook endpoint
6. **Webhook Validation**: Verify Freshdesk signatures
7. **Multi-Region**: Active-active deployment
8. **Caching**: Cache frequently accessed tickets
