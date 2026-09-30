# Odoo Mail Sync for Microsoft 365

Mirrors Microsoft 365 mailboxes into Odoo Community and links every conversation to the
matching contacts and their open CRM opportunities – the way Pipedrive's Email Sync works,
plus a few smarter rules.

| Module | Purpose |
|---|---|
| `mail_sync_microsoft` | Microsoft Graph connection (app-only or per-user), mailbox and folder management, delta sync, participant matching to contacts, visibility rules, routing log |
| `crm_mail_sync` | Links conversations to CRM opportunities (`crm.lead`): unique-open-deal rule, deal references in subjects, scoring for ambiguous cases, suggestions instead of silent drops |

* Odoo 18.0 (branch `18.0`) and 19.0 (branch `19.0`), Community edition, self-hosted
* License: LGPL-3
* Maintained by Kneissl Messtechnik GmbH – contributions welcome

Design: [docs/superpowers/specs/2026-09-30-mail-sync-design.md](docs/superpowers/specs/2026-09-30-mail-sync-design.md)

Status: design phase – no installable code yet.

## Development

Requirements: Python 3.12, Docker, and a PostgreSQL 16 reachable from the host (for tests).

```bash
make install-hooks   # pip install -r requirements-dev.txt + pre-commit install
make lint            # ruff check + ruff format --check
make format          # auto-fix lint findings and formatting
make pre-commit      # all hooks (ruff, ruff-format, pylint-odoo mandatory rules, file checks)
make test            # Odoo 18 tests of both modules in the odoo:18.0 image
```

`make test` runs the same command as CI (`.github/workflows/ci.yml`): it clones
[OCA/queue](https://github.com/OCA/queue) 18.0 into `.oca/queue` on first use, starts the
`odoo:18.0` image with `--network host`, installs `msal`, installs both modules into a fresh
database and runs their tests. Connection settings are variables with these defaults:

| Variable | Default | Purpose |
|---|---|---|
| `PGHOST` / `PGPORT` | `127.0.0.1` / `5432` | PostgreSQL reachable from the host network |
| `PGUSER` / `PGPASSWORD` | `odoo` / `odoo` | role that may create the test database |
| `TEST_DB` | `mail_sync_test` | database created (must not exist yet) |
| `ODOO_IMAGE` | `odoo:18.0` | image to run; use `odoo:19.0` on the 19.0 branch |
| `OCA_QUEUE` | `.oca/queue` | local checkout of OCA/queue (`queue_job`) |

Example against a Postgres on another port: `make test PGPORT=5433 PGPASSWORD=secret`.
Drop the database before re-running: `dropdb -h 127.0.0.1 -U odoo mail_sync_test`.
