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
