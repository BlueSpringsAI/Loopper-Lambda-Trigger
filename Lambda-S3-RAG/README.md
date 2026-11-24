# Lambda-S3-RAG

S3-triggered knowledge-base ingestion for Loopper-AI, deployed as the `loopper-ai-<env>-s3-to-rag` Lambda. When a document is uploaded to or deleted from the knowledge-base S3 bucket, it ingests the file into the RAG service or removes its chunks.

## Architecture

```
KB upload API / UI → S3 bucket
                       │  ObjectCreated / ObjectRemoved event
                       ▼
               s3-to-rag (this Lambda)
                       │
                       ├─ ObjectCreated → POST /api/v1/ingest/file
                       │                  POST /api/v1/documents/activate  (versioned documents only)
                       └─ ObjectRemoved → DELETE /api/v1/sources
                       ▼
                  RAG service
```

## Project Structure

```
Lambda-S3-RAG/
├── handler.py    # Deployed Lambda code (entry point: handler.handler)
└── README.md
```

The entry-point function is `handler` (not `lambda_handler` like the other two Lambdas). Dependencies: Python stdlib plus `boto3` (provided by the Lambda Python runtime).

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RAG_SERVICE_URL` | Yes* | `http://localhost:8001` | Base URL of the RAG service |
| `INTERNAL_API_KEY` | Yes* | empty | Service key sent as the `X-Internal-API-Key` header on every RAG call |
| `REQUEST_TIMEOUT` | No | `600` | HTTP timeout in seconds for RAG calls |
| `MAX_FILE_SIZE_MB` | No | `50` | Files larger than this are skipped |

*The code has defaults, but the `localhost` URL only suits local runs, and the RAG API rejects callers that have neither a sign-in nor this service key.

The function's IAM role also needs `s3:GetObject` on the bucket, since the handler downloads each new object.

## S3 Key Layout

Keys are written by the KB upload API / UI:

| Key | Collection | Scope |
|-----|------------|-------|
| `<collection>/<filename>` | `<collection>` | common |
| `<collection>/groups/<team_key>/<filename>` | `<collection>` | group (`team_key` = `<team_key>`) |
| `<filename>` | RAG service default | common |

The first path segment is the collection. A `groups/<team_key>/` segment after it marks a group-scoped document. Keys are URL-decoded from the S3 event before parsing.

## Event Flow

### ObjectCreated

1. Skip the file if its extension is not supported (see below) or its size (from the event) is over `MAX_FILE_SIZE_MB`
2. Download the object from S3
3. Build the ingest metadata from the key and the object's S3 metadata:
   - `source` (filename), `s3_bucket`, `s3_key`
   - `scope`: S3 metadata `scope` if set, else `group` when there is a `team_key`, else `common`
   - `team_key`: from the key path, else S3 metadata `team_key` / `team-key`
   - `doc_version`: S3 metadata `doc_version` / `doc-version` (stamped by the KB upload API)
4. `POST {RAG_SERVICE_URL}/api/v1/ingest/file` as `multipart/form-data` with `file`, `metadata` (JSON) and `collection` (when the key has one)
5. If the object has a `doc_version`, `POST {RAG_SERVICE_URL}/api/v1/documents/activate` with `{source, doc_version, team_key?, collection?}` so the new version becomes the single active one (the RAG service deactivates the rest)

Supported extensions: `.pdf`, `.docx`, `.doc`, `.pptx`, `.ppt`, `.xlsx`, `.xls`, `.html`, `.htm`, `.md`, `.markdown`, `.txt`, `.json`.

### ObjectRemoved

`DELETE {RAG_SERVICE_URL}/api/v1/sources` with query parameters `source` (filename), `layer` (`group` if the key has a `team_key`, else `common`), plus `team_key` and `collection` when present. Scoping the delete to the layer means a common file and a group file with the same filename never delete each other's chunks.

Other S3 event types are logged and ignored.

## Error Handling

| Call | Response | Behaviour |
|------|----------|-----------|
| Ingest | 400 | Logged, not retried (bad format, empty file, etc.) |
| Ingest | other HTTP error, or network error | Re-raised so the invocation fails and Lambda retries it |
| Activate | HTTP error | Logged only; not re-raised, so a retry does not ingest the same file again |
| Activate | network error | Not caught: the invocation fails and the Lambda retry ingests the file again |
| Delete | 404 | Treated as already deleted |
| Delete | 400 | Logged, not retried |
| Delete | other HTTP error, or network error | Re-raised so the invocation fails and Lambda retries it |

## Deployment

Deployed by GitHub Actions. On every push that changes `Lambda-S3-RAG/handler.py` (or `Lambda-Webhook/handler.py` / `Lambda-SQS/handler.py`), and on manual `workflow_dispatch`, CI zips this folder's `handler.py` and updates the function code:

| Branch | Workflow | Function |
|--------|----------|----------|
| `dev` | `.github/workflows/lambdas-dev-deploy.yaml` | `loopper-ai-dev-s3-to-rag` |
| `main` | `.github/workflows/lambdas-actions.yaml` | `loopper-ai-staging-s3-to-rag` |

Region: `eu-central-1`. CI only updates the code; environment variables, the handler setting and the S3 trigger are configured on the function outside this repo.

### Manual (same steps as CI)

```bash
cd Lambda-S3-RAG
zip -q ../s3-to-rag.zip handler.py

aws lambda update-function-code \
  --function-name loopper-ai-<env>-s3-to-rag \
  --zip-file fileb://../s3-to-rag.zip \
  --region eu-central-1
```

## License

Copyright 2025 Loopper-AI
