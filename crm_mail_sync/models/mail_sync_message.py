# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, fields, models


class MailSyncMessage(models.Model):
    _inherit = "mail.sync.message"

    lead_id = fields.Many2one("crm.lead", string="Verkaufschance", compute="_compute_lead_id", store=True, index=True)
    candidate_lead_ids = fields.Many2many(
        "crm.lead", "mail_sync_message_candidate_rel", "message_id", "lead_id", string="Kandidaten"
    )
    has_candidates = fields.Boolean(compute="_compute_has_candidates", store=True)
    lead_proposal = fields.Boolean(string="Lead vorgeschlagen", default=False)

    @api.depends("model", "res_id")
    def _compute_lead_id(self):
        for message in self:
            message.lead_id = message.res_id if message.model == "crm.lead" and message.res_id else False

    @api.depends("candidate_lead_ids")
    def _compute_has_candidates(self):
        for message in self:
            message.has_candidates = bool(message.candidate_lead_ids)

    def action_link_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Konversation verknüpfen"),
            "res_model": "mail.sync.link.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_message_ids": [(6, 0, self.ids)]},
        }

    def action_create_lead(self):
        """Create an opportunity from a proposed conversation and link it."""
        Lead = self.env["crm.lead"]
        for message in self:
            partner = message.partner_ids[:1]
            lead = Lead.create(
                {
                    "name": message.subject or self.env._("Anfrage von %s", message.email_from),
                    "type": "opportunity",
                    "partner_id": partner.id if partner else False,
                    "email_from": message.email_from if not partner else False,
                    "team_id": message.mailbox_id.lead_team_id.id or False,
                    "user_id": message.mailbox_id.owner_user_id.id or False,
                    "description": message.body_preview or False,
                }
            )
            message._link_conversation(lead)
        return True

    def _link_conversation(self, lead):
        """Link this message and every other row of the same conversation to ``lead``."""
        for message in self:
            rows = message
            if message.conversation_id:
                rows |= self.search([("conversation_id", "=", message.conversation_id)])
            rows.write({"candidate_lead_ids": [(5, 0, 0)], "lead_proposal": False})
            rows.action_link_to(lead)
        return True
