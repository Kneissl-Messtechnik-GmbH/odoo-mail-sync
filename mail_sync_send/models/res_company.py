# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    mail_sync_personal_signature_id = fields.Many2one(
        "mail.sync.signature",
        string="Standard-Signatur (persönlich)",
        domain=[("usage", "=", "personal")],
        help="Gilt für alle Benutzer mit zentral verwalteter Signatur, die keine eigene Vorlage gewählt haben.",
    )
    mail_sync_shared_signature_id = fields.Many2one(
        "mail.sync.signature",
        string="Standard-Signatur (Funktionspostfächer)",
        domain=[("usage", "=", "shared")],
        help="Gilt für Funktionspostfächer ohne eigene Signatur.",
    )
    mail_sync_logo_attachment_id = fields.Many2one("ir.attachment", copy=False, readonly=True)

    def _mail_sync_logo_url(self):
        """Public, stable URL of the company logo for signatures (empty without logo)."""
        self.ensure_one()
        company = self.sudo()  # sudo: logo snapshot is public by design
        if not company.logo:
            return ""
        Signature = self.env["mail.sync.signature"]
        attachment = Signature._store_public_image(
            company.mail_sync_logo_attachment_id, company.logo, f"logo-{company.id}.png"
        )
        if company.mail_sync_logo_attachment_id != attachment:
            company.mail_sync_logo_attachment_id = attachment
        return Signature._public_image_url(attachment)

    def write(self, vals):
        res = super().write(vals)
        if {
            "mail_sync_personal_signature_id",
            "name",
            "phone",
            "email",
            "website",
            "street",
            "zip",
            "city",
            "logo",
        } & set(vals):
            users = (
                self.env["res.users"]
                .sudo()
                .search([("mail_sync_signature_managed", "=", True), ("company_id", "in", self.ids)])
            )
            users._mail_sync_apply_signature()
        return res
