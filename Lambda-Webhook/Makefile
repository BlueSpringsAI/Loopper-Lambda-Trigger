# Makefile for Lambda-Webhook
# Copyright 2025 Loopper-AI

.PHONY: help install build deploy test clean package lint format

help:
	@echo "Lambda-Webhook - Available commands:"
	@echo "  make install   - Install dependencies"
	@echo "  make build     - Build SAM application"
	@echo "  make deploy    - Deploy to AWS"
	@echo "  make test      - Run tests"
	@echo "  make lint      - Run code linting"
	@echo "  make format    - Format code"
	@echo "  make package   - Create deployment package"
	@echo "  make clean     - Remove build artifacts"

install:
	@echo "Installing dependencies..."
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

build:
	@echo "Building SAM application..."
	sam build

deploy: build
	@echo "Deploying to AWS..."
	sam deploy

deploy-guided: build
	@echo "Deploying with guided prompts..."
	sam deploy --guided

test:
	@echo "Running tests..."
	python -m pytest tests/ -v

lint:
	@echo "Running linters..."
	mypy src/
	ruff check src/

format:
	@echo "Formatting code..."
	black src/
	ruff check --fix src/

package:
	@echo "Creating deployment package..."
	cd src && zip -r ../lambda-webhook.zip . -x "*.pyc" -x "__pycache__/*"
	@echo "Package created: lambda-webhook.zip"

clean:
	@echo "Cleaning build artifacts..."
	rm -rf .aws-sam/
	rm -f lambda-webhook.zip
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean complete"

logs:
	@echo "Tailing Lambda logs..."
	sam logs --stack-name loopper-lambda-webhook --tail

validate:
	@echo "Validating SAM template..."
	sam validate

local-invoke:
	@echo "Invoking Lambda locally..."
	sam local invoke -e events/test-created.json
