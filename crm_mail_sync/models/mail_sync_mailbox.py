# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class MailSyncMailbox(models.Model):
    _inherit = "mail.sync.mailbox"

    is_sales_mailbox = fields.Boolean(
        string="Vertriebspostfach",
        help="Neue Konversationen ohne offene Verkaufschance werden als Lead vorgeschlagen oder angelegt.",
    )
    lead_proposal = fields.Selection(
        [("suggest", "Lead vorschlagen"), ("create", "Lead automatisch anlegen")],
        string="Neue Anfragen",
        default="suggest",
        required=True,
    )
    lead_team_id = fields.Many2one("crm.team", string="Verkaufsteam für neue Leads")
