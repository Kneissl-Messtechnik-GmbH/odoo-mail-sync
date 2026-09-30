# Odoo Mail Sync for Microsoft 365 – developer tasks
#
# Tests run the same command as CI (.github/workflows/ci.yml) inside the official
# odoo:18.0 image, using --network host against a Postgres reachable from the host.
# Override any of the variables below on the command line or via the environment,
# e.g.  make test PGHOST=127.0.0.1 PGPORT=5433 PGPASSWORD=secret

SHELL := /bin/bash
.DEFAULT_GOAL := help

MODULES     ?= mail_sync_microsoft,crm_mail_sync
TEST_TAGS   ?= /mail_sync_microsoft,/crm_mail_sync
ODOO_IMAGE  ?= odoo:18.0
PGHOST      ?= 127.0.0.1
PGPORT      ?= 5432
PGUSER      ?= odoo
PGPASSWORD  ?= odoo
TEST_DB     ?= mail_sync_test
OCA_QUEUE   ?= $(CURDIR)/.oca/queue
OCA_BRANCH  ?= 18.0

ODOO_ADDONS_PATH := /usr/lib/python3/dist-packages/odoo/addons,/mnt/extra-addons,/mnt/oca-queue

.PHONY: help lint format pre-commit install-hooks oca-queue test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

lint: ## Ruff lint + format check
	@echo "=== Ruff Lint ==="
	ruff check . --config ruff.toml
	@echo "=== Ruff Format Check ==="
	ruff format --check . --config ruff.toml

format: ## Auto-fix with Ruff (lint fixes + formatting)
	ruff check . --config ruff.toml --fix
	ruff format . --config ruff.toml

pre-commit: ## Run all pre-commit hooks on all files
	pre-commit run --all-files --show-diff-on-failure

install-hooks: ## Install dev tools and activate the pre-commit git hook
	python3 -m pip install -r requirements-dev.txt
	pre-commit install

oca-queue: ## Clone OCA/queue (queue_job dependency) into $(OCA_QUEUE) if missing
	@if [ ! -d "$(OCA_QUEUE)/queue_job" ]; then \
		git clone https://github.com/OCA/queue.git -b $(OCA_BRANCH) --depth 1 "$(OCA_QUEUE)"; \
	fi

test: oca-queue ## Run module tests in the odoo image against a local Postgres (see variables above)
	docker run --rm --network host --user root \
		-e PYTHONDONTWRITEBYTECODE=1 \
		-v "$(CURDIR):/mnt/extra-addons:ro" \
		-v "$(OCA_QUEUE):/mnt/oca-queue:ro" \
		--entrypoint bash $(ODOO_IMAGE) -c '\
			pip install --break-system-packages -q msal && \
			odoo --db_host=$(PGHOST) --db_port=$(PGPORT) --db_user=$(PGUSER) --db_password=$(PGPASSWORD) \
				-d $(TEST_DB) \
				--addons-path=$(ODOO_ADDONS_PATH) \
				-i $(MODULES) --test-enable --test-tags $(TEST_TAGS) \
				--stop-after-init --without-demo=all --no-http --log-level=test'
