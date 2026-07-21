# GameGusto — common tasks.
#
# Infrastructure runs as the scoped gamegusto-deploy role, never as admin.
# See infra/README.md for why, and for the one-time bootstrap.

SHELL       := /bin/bash
PROFILE     ?= gamegusto-deploy
STACK       := infra/stack
TF          ?= terraform
NODE_BIN    := $(HOME)/.local/nodejs/bin
LOGIN_EMAIL ?= christian.pavese@gmail.com

# Every stack command needs the login email; keep it in one place.
TF_STACK := AWS_PROFILE=$(PROFILE) $(TF) -chdir=$(STACK)
TF_VARS  := -var login_email=$(LOGIN_EMAIL)

.PHONY: help check test lint types web-build api-build plan apply deploy deploy-web deploy-api url

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# --- quality ---------------------------------------------------------------

check: lint types test ## Run the full gate (lint, types, tests)

test: ## Python tests with the coverage gate
	.venv/bin/python -m pytest -q

lint: ## ruff check + format check
	.venv/bin/python -m ruff check .
	.venv/bin/python -m ruff format --check .

types: ## mypy
	.venv/bin/python -m mypy .

# --- build -----------------------------------------------------------------

web-build: ## Type-check and bundle the PWA (with Cognito config from the stack)
	@set -euo pipefail; \
	domain=$$($(TF_STACK) output -raw login_domain 2>/dev/null || echo ""); \
	client=$$($(TF_STACK) output -raw user_pool_client_id 2>/dev/null || echo ""); \
	if [[ -z "$$domain" ]]; then \
	  echo "!! no Cognito outputs — building UNAUTHENTICATED (fine for local dev)"; \
	fi; \
	cd web && PATH="$(NODE_BIN):$$PATH" \
	  VITE_COGNITO_DOMAIN="$$domain" VITE_COGNITO_CLIENT_ID="$$client" npm run build

api-build: ## Build the Lambda deployment zip
	./scripts/build_lambda.sh

# --- infrastructure --------------------------------------------------------

plan: api-build ## Show what would change
	$(TF_STACK) plan $(TF_VARS)

apply: api-build ## Apply infrastructure (rebuilds the Lambda bundle first)
	$(TF_STACK) apply $(TF_VARS)

url: ## Print the app URL
	@$(TF_STACK) output -raw app_url

# --- deploy ----------------------------------------------------------------

deploy: deploy-api deploy-web ## Ship everything

deploy-api: apply ## Ship the API (bundle + infrastructure)
	@echo "API deployed."

deploy-web: web-build ## Ship the PWA: sync to S3, then invalidate
	@set -euo pipefail; \
	bucket=$$($(TF_STACK) output -raw site_bucket); \
	dist=$$($(TF_STACK) output -raw distribution_id); \
	echo "==> syncing web/dist to $$bucket"; \
	AWS_PROFILE=$(PROFILE) aws s3 sync web/dist "s3://$$bucket" --delete \
	  --cache-control "public,max-age=31536000,immutable" \
	  --exclude "index.html" --exclude "*.webmanifest" --exclude "sw.js"; \
	echo "==> uploading entry points (must not be cached)"; \
	AWS_PROFILE=$(PROFILE) aws s3 cp web/dist/index.html "s3://$$bucket/index.html" \
	  --cache-control "no-cache" --content-type "text/html"; \
	AWS_PROFILE=$(PROFILE) aws s3 cp web/dist/manifest.webmanifest "s3://$$bucket/manifest.webmanifest" \
	  --cache-control "no-cache" --content-type "application/manifest+json"; \
	: "sw.js decides what every other file is allowed to be. Cached, it would" ; \
	: "pin the app to an old worker with no way to push a fix."               ; \
	AWS_PROFILE=$(PROFILE) aws s3 cp web/dist/sw.js "s3://$$bucket/sw.js" \
	  --cache-control "no-cache" --content-type "text/javascript"; \
	echo "==> invalidating"; \
	AWS_PROFILE=$(PROFILE) aws cloudfront create-invalidation \
	  --distribution-id "$$dist" --paths "/index.html" "/manifest.webmanifest" "/sw.js" \
	  --query 'Invalidation.Status' --output text
