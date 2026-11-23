# Loopper-Lambda-Trigger

AWS Lambda functions that feed Loopper-AI: Freshdesk webhooks into the app server, and knowledge-base documents into the RAG service.

## Lambdas

| Folder | Function (`<env>` = `dev` / `staging`) | Trigger | What it does |
|--------|------------------------------------------|---------|--------------|
| [`Lambda-Webhook/`](Lambda-Webhook/README.md) | `loopper-ai-<env>-api-to-sqs` | API Gateway (Freshdesk webhook) | Queues the webhook as-is to SQS FIFO |
| [`Lambda-SQS/`](Lambda-SQS/README.md) | `loopper-ai-<env>-pipe-to-app-server` | SQS event source mapping | POSTs the SQS batch to the app server's `/queue/webhook` |
| [`Lambda-S3-RAG/`](Lambda-S3-RAG/README.md) | `loopper-ai-<env>-s3-to-rag` | S3 `ObjectCreated` / `ObjectRemoved` | Ingests uploaded KB documents into the RAG service, or deletes their chunks |

## How They Connect

```
Freshdesk webhook → API Gateway → api-to-sqs → SQS FIFO → pipe-to-app-server → App server (/queue/webhook)
                                                                                   └─ fetches the ticket from Freshdesk

KB upload API / UI → S3 → s3-to-rag → RAG service (/api/v1/ingest/file, /api/v1/documents/activate, /api/v1/sources)
```

`api-to-sqs` does not call Freshdesk; the app server is the only Freshdesk API caller.

## Repository Layout

```
.github/workflows/
├── lambdas-dev-deploy.yaml   # push to dev  → loopper-ai-dev-*
└── lambdas-actions.yaml      # push to main → loopper-ai-staging-*
Lambda-Webhook/handler.py     # api-to-sqs
Lambda-SQS/handler.py         # pipe-to-app-server
Lambda-S3-RAG/handler.py      # s3-to-rag
```

### `handler.py`-at-root convention

Each Lambda's deployed code is the single `handler.py` at the root of its folder. CI zips that one file and nothing else, so a handler may only use the Python standard library and `boto3` (provided by the Lambda Python runtime).

`Lambda-Webhook/` and `Lambda-SQS/` also contain a `src/` package, `template.yaml` (SAM), `Dockerfile` and `Makefile` from an earlier modular version. These are **not deployed** and do not match the live handlers; change the root `handler.py` instead.

## CI / Deployment

| Branch | Workflow | Functions updated |
|--------|----------|-------------------|
| `dev` | `.github/workflows/lambdas-dev-deploy.yaml` | `loopper-ai-dev-api-to-sqs`, `loopper-ai-dev-pipe-to-app-server`, `loopper-ai-dev-s3-to-rag` |
| `main` | `.github/workflows/lambdas-actions.yaml` | `loopper-ai-staging-api-to-sqs`, `loopper-ai-staging-pipe-to-app-server`, `loopper-ai-staging-s3-to-rag` |

- **Triggers**: a push to the branch that changes any of `Lambda-Webhook/handler.py`, `Lambda-SQS/handler.py` or `Lambda-S3-RAG/handler.py`, or a manual `workflow_dispatch`.
- **What a run does**: zips each `handler.py`, then for each of the three functions runs `aws lambda update-function-code` and waits for `function-updated`. All three functions are updated on every run, whichever handler changed.
- **Region**: `eu-central-1`.
- **Credentials**: GitHub secrets `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
- **Scope**: CI only updates function code. Environment variables, triggers and other function configuration are managed outside this repo.

## Per-Lambda Docs

- [Lambda-Webhook (api-to-sqs)](Lambda-Webhook/README.md)
- [Lambda-SQS (pipe-to-app-server)](Lambda-SQS/README.md)
- [Lambda-S3-RAG (s3-to-rag)](Lambda-S3-RAG/README.md)
