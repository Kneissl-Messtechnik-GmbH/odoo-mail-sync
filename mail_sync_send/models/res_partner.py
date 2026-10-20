# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import models

SIGNATURE_PARTNER_FIELDS = {"name", "firstname", "lastname", "function", "phone", "mobile", "email", "image_1920"}


class ResPartner(models.Model):
    _inherit = "res.partner"

    def write(self, vals):
        res = super().write(vals)
        if SIGNATURE_PARTNER_FIELDS & set(vals):
            # sudo: refresh the generated signatures of the users behind these contacts
            users = self.sudo().user_ids.filtered("mail_sync_signature_managed")
            if users:
                users._mail_sync_apply_signature()
        return res
