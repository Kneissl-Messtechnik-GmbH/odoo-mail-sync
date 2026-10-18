# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, fields, models


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    mail_sync_send_mailbox_id = fields.Many2one(
        "mail.sync.mailbox",
        string="Senden über",
        default=lambda self: self._default_mail_sync_send_mailbox(),
        help="Postfach, aus dem die Mail verschickt wird. Bei Funktionspostfächern steht die sendende Person "
        "in der Signatur. Leer: Standardverhalten von Odoo.",
    )
    mail_sync_allowed_mailbox_ids = fields.Many2many(
        "mail.sync.mailbox", compute="_compute_mail_sync_allowed_mailbox_ids"
    )

    @api.model
    def _default_mail_sync_send_mailbox(self):
        model = self.env.context.get("default_model")
        return self.env["mail.sync.mailbox"]._mail_sync_default_for(model)

    def _compute_mail_sync_allowed_mailbox_ids(self):
        allowed = self.env["mail.sync.mailbox"]._mail_sync_sendable()
        for composer in self:
            composer.mail_sync_allowed_mailbox_ids = allowed

    @api.depends(
        "composition_mode",
        "email_from",
        "model",
        "res_domain",
        "res_ids",
        "template_id",
        "mail_sync_send_mailbox_id",
    )
    def _compute_authorship(self):
        super()._compute_authorship()
        for composer in self.filtered("mail_sync_send_mailbox_id"):
            mailbox = composer.mail_sync_send_mailbox_id
            if not mailbox._mail_sync_can_send(self.env.user):
                continue
            composer.email_from = mailbox._mail_sync_from_address(self.env.user)
            # the person stays the author in the chatter, only the sending address changes
            composer.author_id = self.env.user.partner_id
