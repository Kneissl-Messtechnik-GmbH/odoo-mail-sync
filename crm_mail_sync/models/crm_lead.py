# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from datetime import timedelta

from markupsafe import Markup
from odoo import api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    mail_sync_message_ids = fields.One2many("mail.sync.message", "lead_id", string="Synchronisierte E-Mails")
    mail_sync_count = fields.Integer(compute="_compute_mail_sync_count")
    mail_sync_suggestion_count = fields.Integer(compute="_compute_mail_sync_count")

    def _compute_mail_sync_count(self):
        Message = self.env["mail.sync.message"]
        for lead in self:
            lead.mail_sync_count = Message.search_count([("lead_id", "=", lead.id)])
            lead.mail_sync_suggestion_count = Message.search_count([("candidate_lead_ids", "in", lead.ids)])

    def action_view_mail_sync_messages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("E-Mails"),
            "res_model": "mail.sync.message",
            "view_mode": "list,form",
            "domain": [("lead_id", "=", self.id)],
        }

    def action_view_mail_sync_suggestions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Vorgeschlagene Konversationen"),
            "res_model": "mail.sync.message",
            "view_mode": "list,form",
            "domain": [("candidate_lead_ids", "in", self.ids)],
            "context": {"default_lead_id": self.id, "mail_sync_lead_id": self.id},
        }

    # D6: retro suggestions when an opportunity is created for a known contact
    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        if not self.env.context.get("mail_sync_import"):
            leads.filtered(lambda lead: lead.partner_id and lead.type == "opportunity")._mail_sync_retro_suggest()
        return leads

    def _mail_sync_retro_suggest(self):
        Message = self.env["mail.sync.message"].sudo()  # sudo: rows of other users' shared mailboxes count too
        for lead in self:
            account_days = lead.env["mail.sync.account"].sudo().search([], limit=1).retro_days or 30
            since = fields.Datetime.now() - timedelta(days=account_days)
            partners = (
                lead.partner_id
                | lead.partner_id.commercial_partner_id
                | lead.partner_id.commercial_partner_id.child_ids
            )
            rows = Message.search(
                [
                    ("partner_ids", "in", partners.ids),
                    ("received_at", ">=", since),
                    ("visibility", "=", "shared"),
                    ("lead_id", "=", False),
                    ("state", "in", ("linked", "matched")),
                ]
            )
            if not rows:
                continue
            rows.write({"candidate_lead_ids": [(4, lead.id)]})
            conversations = len(set(rows.mapped("conversation_id")) or rows.ids)
            body = Markup("<p>%s</p>") % self.env._(
                "%(count)s E-Mail-Konversation(en) mit %(partner)s aus den letzten %(days)s Tagen gefunden. "
                "Über den Button „Vorschläge“ lassen sie sich dieser Verkaufschance zuordnen.",
                count=conversations,
                partner=lead.partner_id.display_name,
                days=account_days,
            )
            lead.message_post(body=body, message_type="notification", subtype_xmlid="mail.mt_note")
        return True
