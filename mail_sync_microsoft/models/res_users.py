# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import models
from odoo.exceptions import UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    def action_connect_mailbox(self):
        """Delegated mode: create (if needed) and connect the personal mailbox of the current user."""
        self.ensure_one()
        account = self.env["mail.sync.account"].search(
            [("auth_mode", "=", "delegated"), ("active", "=", True)], limit=1
        )
        if not account:
            raise UserError(self.env._("Es ist keine delegierte Microsoft-365-Verbindung eingerichtet."))
        if not self.email:
            raise UserError(self.env._("Ihr Benutzer hat keine E-Mail-Adresse."))
        Mailbox = self.env["mail.sync.mailbox"]
        mailbox = Mailbox.search([("account_id", "=", account.id), ("upn", "=ilike", self.email)], limit=1)
        if not mailbox:
            mailbox = Mailbox.create(
                {"account_id": account.id, "upn": self.email.lower(), "owner_user_id": self.id, "kind": "personal"}
            )
        return mailbox.action_connect()
