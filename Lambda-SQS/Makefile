# Makefile for Lambda-SQS
# Copyright 2025 Loopper-AI

.PHONY: help install build deploy test clean package lint format

help:
	@echo "Lambda-SQS - Available commands:"
	@echo "  make install   - Install dev dependencies"
	@echo "  make build     - Build SAM application"
	@echo "  make deploy    - Deploy to AWS"
	@echo "  make test      - Run tests"
	@echo "  make lint      - Run linters"
	@echo "  make format    - Format code"
	@echo "  make package   - Create deployment zip"
	@echo "  make clean     - Remove build artifacts"

install:
	pip install -r requirements-dev.txt

build:
	sam build

deploy: build
	sam deploy

deploy-guided: build
	sam deploy --guided

test:
	python -m pytest tests/ -v

lint:
	mypy src/
	ruff check src/

format:
	black src/ tests/
	ruff check --fix src/ tests/

package:
	cd src && zip -r ../lambda-sqs.zip . -x "*.pyc" -x "__pycache__/*"
	@echo "Package created: lambda-sqs.zip"

clean:
	rm -rf .aws-sam/
	rm -f lambda-sqs.zip
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete

logs:
	sam logs --stack-name loopper-lambda-sqs --tail

local-invoke:
	sam local invoke -e events/test-sqs-single.json
