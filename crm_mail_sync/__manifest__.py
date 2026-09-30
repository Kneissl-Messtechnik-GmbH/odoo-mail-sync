# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "CRM Mail Sync",
    "version": "18.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Link synchronised Microsoft 365 conversations to CRM opportunities (Pipedrive-style rules, smarter)",
    "author": "Kneissl Messtechnik GmbH",
    "website": "https://github.com/Kneissl-Messtechnik-GmbH/odoo-mail-sync",
    "license": "LGPL-3",
    "depends": ["mail_sync_microsoft", "crm"],
    "data": [
        "security/crm_mail_sync_security.xml",
        "security/ir.model.access.csv",
        "wizards/mail_sync_link_wizard_views.xml",
        "views/mail_sync_account_views.xml",
        "views/mail_sync_mailbox_views.xml",
        "views/mail_sync_message_views.xml",
        "views/crm_lead_views.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": True,
}
