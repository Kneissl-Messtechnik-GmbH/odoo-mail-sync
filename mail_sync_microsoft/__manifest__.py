# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Mail Sync for Microsoft 365",
    "version": "18.0.1.0.0",
    "category": "Discuss",
    "summary": "Mirror Microsoft 365 mailboxes into Odoo and link conversations to contacts (Pipedrive-style)",
    "author": "Kneissl Messtechnik GmbH",
    "website": "https://github.com/Kneissl-Messtechnik-GmbH/odoo-mail-sync",
    "license": "LGPL-3",
    "depends": ["mail", "contacts", "queue_job"],
    "external_dependencies": {"python": ["msal", "requests"]},
    "data": [
        "security/mail_sync_security.xml",
        "security/ir.model.access.csv",
        "data/queue_job_data.xml",
        "data/ir_cron_data.xml",
        "views/mail_sync_account_views.xml",
        "views/mail_sync_mailbox_views.xml",
        "views/mail_sync_message_views.xml",
        "views/mail_sync_run_views.xml",
        "views/res_partner_views.xml",
        "views/mail_sync_menu.xml",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
