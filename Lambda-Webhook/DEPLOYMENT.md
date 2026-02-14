# Deployment Guide - Lambda-Webhook

## Prerequisites

- AWS CLI configured with appropriate credentials
- AWS SAM CLI installed (optional, for SAM deployment)
- Python 3.12 or later
- SQS FIFO queue created
- (Optional) AWS Secrets Manager secret with Freshdesk credentials

## Deployment Methods

### Method 1: AWS SAM (Recommended)

#### Install SAM CLI
```bash
# macOS
brew install aws-sam-cli

# Windows
choco install aws-sam-cli

# Linux
pip install aws-sam-cli
```

#### Deploy

```bash
# Navigate to Lambda-Webhook directory
cd Loopper-Lambda-Trigger/Lambda-Webhook

# Build
sam build

# Deploy with guided prompts
sam deploy --guided

# Follow prompts and provide:
# - Stack name: loopper-lambda-webhook-prod
# - AWS Region: us-east-1 (or your region)
# - Parameter QueueUrl: https://sqs.us-east-1.amazonaws.com/123456789012/your-queue.fifo
# - Parameter SecretsArn: arn:aws:secretsmanager:us-east-1:123456789012:secret:freshdesk
# - Parameter ProcessCreated: true
# - Parameter ProcessUpdated: true
# - Parameter ApiTimeout: 30

# Save configuration for future deployments
# This creates samconfig.toml
```

#### Update Existing Deployment
```bash
# After making changes, rebuild and deploy
sam build
sam deploy
```

#### Deploy to Multiple Environments
```bash
# Development
sam deploy --config-env dev

# Production
sam deploy --config-env prod
```

### Method 2: AWS CLI with CloudFormation

```bash
# Package code
cd src
zip -r ../lambda-webhook.zip .
cd ..

# Upload to S3
aws s3 cp lambda-webhook.zip s3://your-deployment-bucket/lambda-webhook.zip

# Deploy CloudFormation stack
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name loopper-lambda-webhook-prod \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    QueueUrl=https://sqs.us-east-1.amazonaws.com/123456789012/your-queue.fifo \
    SecretsArn=arn:aws:secretsmanager:us-east-1:123456789012:secret:freshdesk \
    ProcessCreated=true \
    ProcessUpdated=true \
    ApiTimeout=30
```

### Method 3: Manual AWS Console

#### Step 1: Create Lambda Function
1. Go to AWS Lambda Console
2. Create function: `loopper-lambda-webhook`
3. Runtime: Python 3.12
4. Architecture: x86_64

#### Step 2: Upload Code
```bash
cd src
zip -r ../lambda-webhook.zip .
```
Upload `lambda-webhook.zip` via Lambda console

#### Step 3: Configure Environment Variables
Add environment variables:
- `QUEUE_URL`: Your SQS FIFO queue URL
- `SECRETS_ARN`: Secrets Manager ARN (optional)
- `PROCESS_CREATED`: `true`
- `PROCESS_UPDATED`: `true`
- `API_TIMEOUT`: `30.0`
- `LOG_LEVEL`: `INFO`

#### Step 4: Configure IAM Role
Attach policies:
- `AWSLambdaBasicExecutionRole`
- Custom policy for SQS:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:REGION:ACCOUNT:QUEUE_NAME.fifo"
    }
  ]
}
```
- Custom policy for Secrets Manager (if using):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "secretsmanager:GetSecretValue",
      "Resource": "arn:aws:secretsmanager:REGION:ACCOUNT:secret:SECRET_NAME"
    }
  ]
}
```

#### Step 5: Create API Gateway
1. Create HTTP API
2. Add POST route: `/webhook`
3. Integration: Lambda function
4. Configure timeout: 29 seconds

#### Step 6: Configure Lambda Settings
- Timeout: 60 seconds
- Memory: 512 MB
- Concurrency: Set reserved concurrency if needed

## Post-Deployment

### Get Webhook URL
```bash
# Using SAM
sam list endpoints --stack-name loopper-lambda-webhook-prod

# Using AWS CLI
aws cloudformation describe-stacks \
  --stack-name loopper-lambda-webhook-prod \
  --query 'Stacks[0].Outputs[?OutputKey==`WebhookApiUrl`].OutputValue' \
  --output text
```

### Configure Freshdesk Webhook
1. Log into Freshdesk admin panel
2. Go to Admin > Workflows > Automations
3. Create new automation or ticket update rule
4. Add action: "Trigger Webhook"
5. URL: Your API Gateway URL (from above)
6. Method: POST
7. Content Type: application/json
8. Events: Ticket Created, Ticket Updated

### Test Webhook

#### Using curl
```bash
curl -X POST https://your-api-id.execute-api.us-east-1.amazonaws.com/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "freshdesk_webhook": {
      "ticket_id": "123",
      "ticket_subject": "Test ticket",
      "ticket_description": "Test description",
      "ticket_contact_email": "customer@example.com",
      "triggered_event": "ticket_created"
    }
  }'
```

#### Expected Response
```json
{
  "status": "accepted",
  "ticket_id": "123",
  "messageId": "abc-123-xyz"
}
```

### Monitor Deployment

#### CloudWatch Logs
```bash
# Tail logs
sam logs --stack-name loopper-lambda-webhook-prod --tail

# Or using AWS CLI
aws logs tail /aws/lambda/loopper-lambda-webhook --follow
```

#### CloudWatch Metrics
Monitor these metrics in CloudWatch:
- `Invocations`: Total webhook calls
- `Errors`: Failed invocations
- `Duration`: Processing time
- `Throttles`: Rate-limited requests

#### CloudWatch Alarms
The SAM template creates these alarms:
- `lambda-errors`: Alerts on 5+ errors in 5 minutes
- `lambda-throttles`: Alerts on any throttling

## Configuration Updates

### Update Environment Variables
```bash
# Using AWS CLI
aws lambda update-function-configuration \
  --function-name loopper-lambda-webhook \
  --environment Variables="{
    QUEUE_URL=https://...,
    SECRETS_ARN=arn:...,
    PROCESS_CREATED=true,
    PROCESS_UPDATED=true,
    API_TIMEOUT=30.0,
    LOG_LEVEL=INFO
  }"
```

### Update Code Only
```bash
# Package new code
cd src && zip -r ../lambda-webhook.zip . && cd ..

# Update function code
aws lambda update-function-code \
  --function-name loopper-lambda-webhook \
  --zip-file fileb://lambda-webhook.zip
```

## Rollback

### Using SAM
```bash
# List previous deployments
aws cloudformation list-stack-resources \
  --stack-name loopper-lambda-webhook-prod

# Delete current stack (will rollback)
sam delete --stack-name loopper-lambda-webhook-prod

# Redeploy previous version
sam deploy --config-env prod
```

### Using Lambda Versions
```bash
# Publish version
aws lambda publish-version \
  --function-name loopper-lambda-webhook

# Update alias to point to previous version
aws lambda update-alias \
  --function-name loopper-lambda-webhook \
  --name prod \
  --function-version 5
```

## Cleanup

```bash
# Using SAM
sam delete --stack-name loopper-lambda-webhook-prod

# Using CloudFormation
aws cloudformation delete-stack \
  --stack-name loopper-lambda-webhook-prod

# Manually delete resources if needed
aws lambda delete-function --function-name loopper-lambda-webhook
```

## Troubleshooting

### Common Issues

1. **SQS Send Permission Denied**
   - Check IAM role has `sqs:SendMessage` permission
   - Verify queue URL is correct

2. **Secrets Manager Access Denied**
   - Check IAM role has `secretsmanager:GetSecretValue` permission
   - Verify secrets ARN is correct

3. **API Gateway Timeout**
   - Increase Lambda timeout (must be > 29s for API Gateway)
   - Optimize Freshdesk API calls

4. **High Error Rate**
   - Check CloudWatch logs for error details
   - Verify Freshdesk webhook payload format
   - Check SQS queue configuration

### Debug Mode
Enable debug logging:
```bash
aws lambda update-function-configuration \
  --function-name loopper-lambda-webhook \
  --environment Variables="{...,LOG_LEVEL=DEBUG}"
```

## Security Best Practices

1. **Secrets Management**
   - Always use AWS Secrets Manager for Freshdesk API keys
   - Enable automatic rotation if supported
   - Use least-privilege IAM policies

2. **API Gateway**
   - Consider adding API key or authorization
   - Enable CloudWatch logging
   - Set throttling limits

3. **Lambda**
   - Use latest Python runtime
   - Enable X-Ray tracing for debugging
   - Set concurrency limits to prevent runaway costs

4. **Monitoring**
   - Set up CloudWatch alarms for errors and throttles
   - Monitor SQS dead-letter queue
   - Review logs regularly

## Performance Optimization

1. **Lambda Configuration**
   - Adjust memory based on CloudWatch metrics
   - Use Provisioned Concurrency for consistent latency
   - Enable Lambda Insights for detailed metrics

2. **Code Optimization**
   - Reuse HTTP connections (boto3 clients)
   - Minimize cold starts with lightweight dependencies
   - Use async/await for concurrent operations

3. **Cost Optimization**
   - Monitor Lambda invocation costs
   - Adjust timeout to actual needs
   - Use reserved concurrency to prevent over-provisioning
