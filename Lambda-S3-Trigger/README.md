# S3-Triggered RAG Ingestion Pipeline

AWS Lambda function that automatically ingests documents into the RAG service when files are uploaded to (or deleted from) S3.

## Architecture

```
Upload:
  S3 PUT s3://bucket/{collection}/file.pdf
    → S3 Event (ObjectCreated)
    → Lambda (downloads file)
    → POST /api/v1/ingest/file
    → Qdrant

Delete:
  S3 DELETE s3://bucket/{collection}/file.pdf
    → S3 Event (ObjectRemoved)
    → Lambda
    → DELETE /api/v1/sources?source=file.pdf&collection={collection}
    → Qdrant (chunks removed)
```

## S3 Prefix → Collection Mapping

The first path segment of the S3 key determines the Qdrant collection:

| S3 Key | Collection | Filename |
|--------|-----------|----------|
| `ecommerce/faq.pdf` | `ecommerce` | `faq.pdf` |
| `policies/2024/returns.md` | `policies` | `returns.md` |
| `file.pdf` (no prefix) | *(default)* | `file.pdf` |

## Supported File Types

`.pdf`, `.docx`, `.doc`, `.pptx`, `.ppt`, `.xlsx`, `.xls`, `.html`, `.htm`, `.md`, `.markdown`, `.txt`, `.json`

Unsupported extensions (e.g., `.png`, `.zip`) are silently skipped.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RAG_SERVICE_URL` | Base URL of the RAG service | `http://localhost:8001` |
| `REQUEST_TIMEOUT` | HTTP request timeout (seconds) | `600` |
| `MAX_FILE_SIZE_MB` | Max file size to process (MB) | `50` |

## Lambda Configuration

| Setting | Value |
|---------|-------|
| Runtime | Python 3.12 |
| Timeout | 900s (15 min) |
| Memory | 512 MB |
| Retries | 2 (async invocation default) |
| DLQ | SQS queue for failed events |

## Deployment

### AWS Resources

1. **S3 Bucket** — `loopper-ai-{env}-rag-documents`
   - Event notifications for `s3:ObjectCreated:*` and `s3:ObjectRemoved:*` → Lambda

2. **Lambda Function** — `loopper-ai-{env}-rag-s3-trigger`
   - IAM role with `s3:GetObject` on the bucket
   - VPC config if RAG service is in a private subnet

3. **SQS DLQ** — `loopper-ai-{env}-rag-s3-trigger-dlq`
   - Lambda async invoke config: max retries = 2, on_failure → DLQ

4. **Lambda Permission** — Allow S3 to invoke the Lambda

5. **CloudWatch Alarm** — Alert on DLQ depth > 0

### Manual Deployment

```bash
# Install dependencies into package directory
cd lambda/s3_trigger
pip install -r requirements.txt -t package/
cd package && zip -r ../deployment.zip . && cd ..
zip deployment.zip handler.py

# Create or update the Lambda function
aws lambda create-function \
  --function-name loopper-ai-dev-rag-s3-trigger \
  --runtime python3.12 \
  --handler handler.handler \
  --zip-file fileb://deployment.zip \
  --role arn:aws:iam::ACCOUNT:role/lambda-s3-trigger-role \
  --timeout 900 \
  --memory-size 512 \
  --environment Variables='{RAG_SERVICE_URL=http://rag-server:8001,REQUEST_TIMEOUT=600,MAX_FILE_SIZE_MB=50}'

# Add S3 trigger permission
aws lambda add-permission \
  --function-name loopper-ai-dev-rag-s3-trigger \
  --statement-id s3-trigger \
  --action lambda:InvokeFunction \
  --principal s3.amazonaws.com \
  --source-arn arn:aws:s3:::loopper-ai-dev-rag-documents

# Configure S3 bucket notification (via console or CLI)
aws s3api put-bucket-notification-configuration \
  --bucket loopper-ai-dev-rag-documents \
  --notification-configuration '{
    "LambdaFunctionConfigurations": [
      {
        "LambdaFunctionArn": "arn:aws:lambda:REGION:ACCOUNT:function:loopper-ai-dev-rag-s3-trigger",
        "Events": ["s3:ObjectCreated:*", "s3:ObjectRemoved:*"]
      }
    ]
  }'
```

### Terraform

Infrastructure can also be provisioned via Terraform. Key resources:

- `aws_lambda_function`
- `aws_lambda_permission` (source: S3)
- `aws_s3_bucket_notification`
- `aws_sqs_queue` (DLQ)
- `aws_lambda_function_event_invoke_config` (retry + DLQ)
- `aws_cloudwatch_metric_alarm` (DLQ depth)
- `aws_iam_role` + `aws_iam_role_policy` (S3 read + SQS write + VPC access)

## Testing

### Unit Tests

```bash
cd /path/to/rag-service
python -m pytest lambda/tests/ -v
```

### End-to-End

```bash
# 1. Upload a test file
aws s3 cp test.pdf s3://loopper-ai-dev-rag-documents/ecommerce/test.pdf

# 2. Check Lambda logs
aws logs tail /aws/lambda/loopper-ai-dev-rag-s3-trigger --follow

# 3. Verify ingestion
curl $RAG_SERVICE_URL/api/v1/sources?collection=ecommerce
# → should list "test.pdf"

# 4. Delete the file
aws s3 rm s3://loopper-ai-dev-rag-documents/ecommerce/test.pdf

# 5. Verify deletion
curl $RAG_SERVICE_URL/api/v1/sources?collection=ecommerce
# → "test.pdf" should be gone
```

## Error Handling

| Error | Behavior | Retried? |
|-------|----------|----------|
| Unsupported file extension | Skip, return success | No |
| File too large | Skip, return success | No |
| S3 download failure | Exception propagates | Yes (2x) |
| RAG 400 (bad request) | Log, return success | No |
| RAG 500/503 | Exception propagates | Yes (2x) |
| RAG unreachable | Exception propagates | Yes (2x) |
| DELETE 404 (not found) | Log info, return success | No |
| All retries exhausted | Event → SQS DLQ | Manual |

**Idempotency**: The RAG service uses UUID5 point IDs based on content hashes. Uploading the same file twice produces the same Qdrant point IDs — upsert is naturally idempotent.

## Monitoring

- **CloudWatch Logs**: All Lambda invocations logged with bucket, key, collection, and outcome
- **CloudWatch Metrics**: Lambda invocations, errors, duration, throttles
- **DLQ Depth Alarm**: Alerts when failed events accumulate
- **RAG Service Logs**: Ingestion details visible in RAG service container logs

## Troubleshooting

| Symptom | Check |
|---------|-------|
| File uploaded but not ingested | Lambda logs for errors; check file extension and size |
| Lambda timeout | Increase timeout; check RAG service responsiveness |
| DLQ messages accumulating | Inspect DLQ messages; check RAG service health |
| Wrong collection | Verify S3 key prefix matches intended collection |
| Permission denied on S3 | Verify Lambda IAM role has `s3:GetObject` on bucket |
| Network error to RAG | Verify Lambda VPC config and security groups |
