# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

SIGNATURE_USER_FIELDS = {
    "mail_sync_signature_id",
    "mail_sync_signature_managed",
    "mail_sync_signature_extra",
    "company_id",
}


class ResUsers(models.Model):
    _inherit = "res.users"

    mail_sync_signature_managed = fields.Boolean(
        string="Signatur zentral verwalten",
        default=True,
        help="Die E-Mail-Signatur wird aus der Signaturvorlage erzeugt und bei Änderungen an Vorlage, "
        "Kontaktdaten oder Profilbild automatisch aktualisiert.",
    )
    mail_sync_signature_id = fields.Many2one(
        "mail.sync.signature",
        string="Signaturvorlage",
        domain=[("usage", "=", "personal")],
        help="Leer: Standard-Signatur der Firma.",
    )
    mail_sync_signature_extra = fields.Html(
        string="Persönlicher Zusatz",
        help="Erscheint an der Stelle {{extra}} der Vorlage, z. B. ein Hinweis auf Abwesenheiten, ein Zitat "
        "oder ein Banner. Bilder werden öffentlich abrufbar gespeichert.",
    )
    mail_sync_photo_attachment_id = fields.Many2one("ir.attachment", copy=False, readonly=True)
    mail_sync_send_mailbox_id = fields.Many2one(
        "mail.sync.mailbox",
        string="Standard-Absender",
        domain=[("send_enabled", "=", True)],
        help="Postfach, über das Nachrichten aus Odoo standardmäßig gesendet werden. Leer: eigenes Postfach.",
    )
    mail_sync_signature_preview = fields.Html(
        compute="_compute_mail_sync_signature_preview", sanitize=False, string="Vorschau"
    )

    @property
    def SELF_READABLE_FIELDS(self):
        return super().SELF_READABLE_FIELDS + [
            "mail_sync_signature_managed",
            "mail_sync_signature_id",
            "mail_sync_signature_extra",
            "mail_sync_send_mailbox_id",
            "mail_sync_signature_preview",
        ]

    @property
    def SELF_WRITEABLE_FIELDS(self):
        return super().SELF_WRITEABLE_FIELDS + [
            "mail_sync_signature_managed",
            "mail_sync_signature_id",
            "mail_sync_signature_extra",
            "mail_sync_send_mailbox_id",
        ]

    @api.depends("mail_sync_signature_managed", "mail_sync_signature_id", "mail_sync_signature_extra", "signature")
    def _compute_mail_sync_signature_preview(self):
        for user in self:
            template = user._mail_sync_signature_template()
            user.mail_sync_signature_preview = template._render_for(user) if template else user.signature

    # ------------------------------------------------------------------ signature
    def _mail_sync_signature_template(self):
        self.ensure_one()
        user = self.sudo()  # sudo: company defaults are configuration
        if not user.mail_sync_signature_managed:
            return self.env["mail.sync.signature"]
        return user.mail_sync_signature_id or user.company_id.mail_sync_personal_signature_id

    def _mail_sync_photo_url(self):
        """Public URL of a snapshot of the profile picture (empty without picture)."""
        self.ensure_one()
        user = self.sudo()  # sudo: the snapshot is public by design, the user chose to put it in signatures
        image = user.partner_id.image_256
        if not image or not user.partner_id.image_1920:
            return ""
        Signature = self.env["mail.sync.signature"]
        attachment = Signature._store_public_image(user.mail_sync_photo_attachment_id, image, f"foto-{user.id}.png")
        if user.mail_sync_photo_attachment_id != attachment:
            super(ResUsers, user).write({"mail_sync_photo_attachment_id": attachment.id})
        return Signature._public_image_url(attachment)

    def _mail_sync_apply_signature(self):
        """Write the rendered template into the native ``signature`` field."""
        for user in self:
            template = user._mail_sync_signature_template()
            if not template:
                continue
            try:
                rendered = template._render_for(user)
            except Exception:
                _logger.exception("Mail Sync: Signatur für %s konnte nicht erzeugt werden", user.login)
                continue
            if str(rendered) != str(user.sudo().signature or ""):
                # sudo: generated value, the user may not have write access to their own record in every flow
                super(ResUsers, user.sudo()).write({"signature": rendered})
        return True

    def action_mail_sync_apply_signature(self):
        self._mail_sync_apply_signature()
        return True

    @api.model
    def _cron_mail_sync_signatures(self):
        self.search([("mail_sync_signature_managed", "=", True), ("share", "=", False)])._mail_sync_apply_signature()
        return True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("mail_sync_signature_extra"):
                vals["mail_sync_signature_extra"] = self.env["mail.sync.signature"]._sanitize_user_html(
                    vals["mail_sync_signature_extra"]
                )
        users = super().create(vals_list)
        users.filtered(lambda u: not u.share and u.mail_sync_signature_managed)._mail_sync_apply_signature()
        return users

    def write(self, vals):
        if vals.get("mail_sync_signature_extra"):
            vals["mail_sync_signature_extra"] = self.env["mail.sync.signature"]._sanitize_user_html(
                vals["mail_sync_signature_extra"]
            )
        res = super().write(vals)
        if SIGNATURE_USER_FIELDS & set(vals):
            self.filtered("mail_sync_signature_managed")._mail_sync_apply_signature()
        return res
