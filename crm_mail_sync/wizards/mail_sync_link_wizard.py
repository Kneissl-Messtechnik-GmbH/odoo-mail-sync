# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, fields, models


class MailSyncLinkWizard(models.TransientModel):
    _name = "mail.sync.link.wizard"
    _description = "Konversation mit Verkaufschance verknüpfen"

    message_ids = fields.Many2many("mail.sync.message", string="Nachrichten", required=True)
    lead_id = fields.Many2one("crm.lead", string="Verkaufschance", domain="[('type', '=', 'opportunity')]")
    candidate_lead_ids = fields.Many2many("crm.lead", compute="_compute_candidates")
    include_conversation = fields.Boolean(string="Ganze Konversation", default=True)

    @api.depends("message_ids")
    def _compute_candidates(self):
        for wizard in self:
            wizard.candidate_lead_ids = wizard.message_ids.mapped("candidate_lead_ids")

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        lead_id = self.env.context.get("mail_sync_lead_id")
        if lead_id and "lead_id" in fields_list:
            vals["lead_id"] = lead_id
        active_ids = self.env.context.get("active_ids") or []
        if self.env.context.get("active_model") == "mail.sync.message" and active_ids and "message_ids" in fields_list:
            vals["message_ids"] = [(6, 0, active_ids)]
        return vals

    def action_link(self):
        self.ensure_one()
        if self.include_conversation:
            self.message_ids._link_conversation(self.lead_id)
        else:
            self.message_ids.write({"candidate_lead_ids": [(5, 0, 0)], "lead_proposal": False})
            self.message_ids.action_link_to(self.lead_id)
        return {"type": "ir.actions.act_window_close"}

    def action_unlink(self):
        self.ensure_one()
        self.message_ids.action_unlink_target()
        return {"type": "ir.actions.act_window_close"}
