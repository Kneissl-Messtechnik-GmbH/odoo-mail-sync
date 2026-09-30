# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    mail_sync_message_ids = fields.Many2many(
        "mail.sync.message",
        "mail_sync_message_partner_rel",
        "partner_id",
        "message_id",
        string="Synchronisierte E-Mails",
    )
    mail_sync_count = fields.Integer(compute="_compute_mail_sync_count")

    def _compute_mail_sync_count(self):
        Message = self.env["mail.sync.message"]
        for partner in self:
            partner.mail_sync_count = Message.search_count(
                ["|", ("partner_ids", "in", partner.ids), ("commercial_partner_ids", "in", partner.ids)]
            )

    def action_view_mail_sync_messages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("E-Mails"),
            "res_model": "mail.sync.message",
            "view_mode": "list,form",
            "domain": ["|", ("partner_ids", "in", self.ids), ("commercial_partner_ids", "in", self.ids)],
        }
