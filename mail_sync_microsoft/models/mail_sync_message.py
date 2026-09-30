# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

STATES = [
    ("skipped_internal", "Übersprungen: intern"),
    ("skipped_blocklist", "Übersprungen: Blockliste"),
    ("skipped_automatic", "Übersprungen: automatisch"),
    ("private", "Privat"),
    ("unmatched", "Kein Kontakt"),
    ("matched", "Kontakt, Zuordnung offen"),
    ("linked", "Zugeordnet"),
    ("removed", "In Microsoft 365 gelöscht"),
    ("error", "Fehler"),
]


class MailSyncMessage(models.Model):
    _name = "mail.sync.message"
    _description = "Mail Sync: Nachricht"
    _order = "received_at desc, id desc"
    _rec_name = "subject"

    mailbox_id = fields.Many2one("mail.sync.mailbox", required=True, ondelete="cascade", index=True)
    folder_id = fields.Many2one("mail.sync.folder", ondelete="set null", index=True)
    owner_user_id = fields.Many2one(related="mailbox_id.owner_user_id", store=True, index=True)
    company_id = fields.Many2one(related="mailbox_id.company_id", store=True)
    graph_id = fields.Char(string="Graph-ID", required=True, index=True)
    internet_message_id = fields.Char(string="Message-ID", index=True)
    conversation_id = fields.Char(string="Konversation", index=True)
    subject = fields.Char()
    email_from = fields.Char(string="Von")
    to_emails = fields.Char(string="An")
    cc_emails = fields.Char(string="Cc")
    received_at = fields.Datetime(string="Empfangen", index=True)
    direction = fields.Selection([("inbound", "Eingehend"), ("outbound", "Ausgehend")], default="inbound")
    has_attachments = fields.Boolean()
    body_preview = fields.Char(size=255)
    web_link = fields.Char(string="In Outlook öffnen")
    state = fields.Selection(STATES, required=True, default="unmatched", index=True)
    reason = fields.Char(string="Grund", help="Warum die Nachricht so eingeordnet wurde.")
    partner_ids = fields.Many2many(
        "res.partner", "mail_sync_message_partner_rel", "message_id", "partner_id", string="Kontakte"
    )
    commercial_partner_ids = fields.Many2many(
        "res.partner", "mail_sync_message_company_rel", "message_id", "partner_id", string="Firmen"
    )
    model = fields.Char(string="Zielmodell", index=True)
    res_id = fields.Integer(string="Ziel-ID", index=True)
    record_display = fields.Char(compute="_compute_record_display", string="Zugeordnet zu")
    mail_message_id = fields.Many2one("mail.message", string="Chatter-Eintrag", ondelete="set null", index=True)
    visibility = fields.Selection([("shared", "Geteilt"), ("private", "Privat")], default="shared", required=True)
    shared_by_id = fields.Many2one("res.users", readonly=True)
    shared_at = fields.Datetime(readonly=True)
    error = fields.Text()

    _sql_constraints = [("graph_id_unique", "unique(mailbox_id, graph_id)", "Nachricht bereits registriert.")]

    @api.depends("model", "res_id")
    def _compute_record_display(self):
        for message in self:
            record = message._target_record()
            message.record_display = record.display_name if record else False

    def _target_record(self):
        self.ensure_one()
        if self.model and self.res_id and self.model in self.env:
            record = self.env[self.model].browse(self.res_id).exists()
            return record or None
        return None

    # ------------------------------------------------------------ actions
    def action_open_record(self):
        self.ensure_one()
        record = self._target_record()
        if not record:
            raise UserError(self.env._("Diese Nachricht ist keinem Datensatz zugeordnet."))
        return {"type": "ir.actions.act_window", "res_model": record._name, "res_id": record.id, "view_mode": "form"}

    def action_share(self):
        """Owner releases a private message: it is imported into the chatter of its target."""
        for message in self:
            if message.visibility == "shared":
                continue
            is_manager = self.env.su or self.env.user.has_group("mail_sync_microsoft.group_mail_sync_manager")
            if message.owner_user_id and message.owner_user_id != self.env.user and not is_manager:
                raise UserError(self.env._("Nur der Eigentümer des Postfachs kann diese Mail freigeben."))
            message.write(
                {"visibility": "shared", "shared_by_id": self.env.user.id, "shared_at": fields.Datetime.now()}
            )
            # sudo: the import job needs the technical models (run log, chatter); it must not run as the sharing user
            message.mailbox_id.sudo().with_delay(
                channel="root.mail_sync", description=f"Mail Sync: Freigabe {message.subject or ''}"[:80]
            ).job_ingest_message(message.id)
        return True

    def action_link_to(self, record):
        """Attach this message (and its imported chatter entry) to ``record``."""
        for message in self:
            vals = {
                "model": record._name,
                "res_id": record.id,
                "state": "linked",
                "reason": self.env._("Manuell zugeordnet"),
            }
            if message.mail_message_id:
                message.mail_message_id.write({"model": record._name, "res_id": record.id})
            message.write(vals)
            if message.visibility == "shared" and not message.mail_message_id:
                # sudo: technical import job, see action_share
                message.mailbox_id.sudo().with_delay(channel="root.mail_sync").job_ingest_message(message.id)
        return True

    def action_unlink_target(self):
        for message in self:
            partner = message.partner_ids[:1]
            target = partner.commercial_partner_id if partner else None
            if message.mail_message_id and target:
                message.mail_message_id.write({"model": target._name, "res_id": target.id})
            message.write(
                {
                    "model": target._name if target else False,
                    "res_id": target.id if target else 0,
                    "state": "matched" if partner else "unmatched",
                    "reason": self.env._("Zuordnung manuell gelöst"),
                }
            )
        return True

    # ------------------------------------------------------ housekeeping
    @api.model
    def _purge_unmatched(self):
        for account in self.env["mail.sync.account"].search([]):
            if account.retention_days <= 0:
                continue
            limit = fields.Datetime.now() - timedelta(days=account.retention_days)
            stale = self.search(
                [
                    ("mailbox_id.account_id", "=", account.id),
                    ("state", "in", ("unmatched", "skipped_internal", "skipped_blocklist", "skipped_automatic")),
                    ("create_date", "<", limit),
                ]
            )
            stale.unlink()
        return True
