# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
{
    "name": "Mail Sync: Send via Microsoft 365 and Signature Manager",
    "version": "18.0.1.0.0",
    "category": "Discuss",
    "summary": "Send Odoo mails through shared or personal Microsoft 365 mailboxes, with central signatures",
    "author": "Kneissl Messtechnik GmbH",
    "website": "https://github.com/Kneissl-Messtechnik-GmbH/odoo-mail-sync",
    "license": "LGPL-3",
    "depends": ["mail", "mail_sync_microsoft"],
    "data": [
        "security/ir.model.access.csv",
        "data/mail_sync_signature_data.xml",
        "data/ir_cron_data.xml",
        "views/mail_sync_signature_views.xml",
        "views/mail_sync_mailbox_views.xml",
        "views/res_users_views.xml",
        "views/res_company_views.xml",
        "views/ir_mail_server_views.xml",
        "views/mail_compose_message_views.xml",
        "views/mail_sync_send_menu.xml",
    ],
    "installable": True,
    "application": False,
    "auto_install": False,
}
