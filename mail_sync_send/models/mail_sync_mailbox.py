# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from email.utils import formataddr

from markupsafe import Markup
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import email_normalize

GRAPH_HOST = "graph.microsoft.com"


class MailSyncMailbox(models.Model):
    _inherit = "mail.sync.mailbox"

    send_enabled = fields.Boolean(
        string="Versand über dieses Postfach",
        tracking=True,
        help="Odoo-Mails mit dieser Absenderadresse werden über Microsoft 365 aus diesem Postfach gesendet "
        "und erscheinen dort in „Gesendete Elemente“.",
    )
    sender_name = fields.Char(
        string="Absendername",
        help="Name im Absender, z. B. „Kneissl Kundenservice“. Leer: Anzeigename des Postfachs.",
    )
    sender_name_mode = fields.Selection(
        [
            ("mailbox", "Nur Postfachname („Kneissl Kundenservice“)"),
            ("user_via", "Person über Postfach („Max Muster | Kneissl Kundenservice“)"),
        ],
        string="Absenderanzeige",
        default="mailbox",
        required=True,
    )
    send_user_ids = fields.Many2many(
        "res.users",
        "mail_sync_mailbox_send_user_rel",
        "mailbox_id",
        "user_id",
        string="Darf senden",
        domain=[("share", "=", False)],
        help="Wer über dieses Funktionspostfach senden darf. Leer: alle internen Benutzer.",
    )
    send_default_model_ids = fields.Many2many(
        "ir.model",
        "mail_sync_mailbox_send_model_rel",
        "mailbox_id",
        "model_id",
        string="Standard-Absender für",
        domain=[("is_mail_thread", "=", True), ("transient", "=", False)],
        ondelete="cascade",
        help="Nachrichten an Kunden auf diesen Datensätzen (z. B. Verkaufschancen, Tickets) gehen standardmäßig "
        "über dieses Postfach, sofern die Person es nutzen darf.",
    )
    signature_id = fields.Many2one(
        "mail.sync.signature",
        string="Signatur",
        domain=[("usage", "=", "shared")],
        help="Signatur für Mails über dieses Funktionspostfach; enthält die sendende Person als Ansprechpartner.",
    )
    mail_server_id = fields.Many2one("ir.mail_server", string="Ausgangsserver", readonly=True, copy=False)
    signature_preview = fields.Html(compute="_compute_signature_preview", sanitize=False, string="Vorschau")

    @api.depends("signature_id", "signature_id.body_html", "sender_name")
    @api.depends_context("uid")
    def _compute_signature_preview(self):
        for mailbox in self:
            mailbox.signature_preview = (
                mailbox._mail_sync_render_signature(self.env.user) if mailbox.kind == "shared" else False
            )

    # ------------------------------------------------------------------ server
    @api.model_create_multi
    def create(self, vals_list):
        mailboxes = super().create(vals_list)
        mailboxes.filtered("send_enabled")._mail_sync_sync_mail_server()
        return mailboxes

    def write(self, vals):
        res = super().write(vals)
        if {"send_enabled", "upn", "active", "display_name", "sender_name"} & set(vals):
            self._mail_sync_sync_mail_server()
        return res

    def _mail_sync_sync_mail_server(self):
        """Keep one Graph-type ir.mail_server per sending mailbox (from_filter = mailbox address)."""
        # sudo: mail servers are system configuration, maintained on behalf of the mailbox manager
        Server = self.env["ir.mail_server"].sudo().with_context(active_test=False)
        for mailbox in self:
            server = mailbox.mail_server_id.sudo() or Server.search(
                [("mail_sync_mailbox_id", "=", mailbox.id)], limit=1
            )
            active = bool(mailbox.send_enabled and mailbox.active)
            if not server and not active:
                continue
            vals = {
                "name": f"Microsoft 365: {mailbox.upn}",
                "smtp_host": GRAPH_HOST,
                "smtp_port": 443,
                "smtp_encryption": "ssl",
                "smtp_authentication": "mail_sync_graph",
                "smtp_user": False,
                "smtp_pass": False,
                "from_filter": mailbox.upn,
                "mail_sync_mailbox_id": mailbox.id,
                "sequence": 5,
                "active": active,
            }
            if server:
                server.write(vals)
            else:
                server = Server.create(vals)
            if mailbox.mail_server_id != server:
                # bypass write() to avoid recursion
                super(MailSyncMailbox, mailbox.sudo()).write({"mail_server_id": server.id})

    # ------------------------------------------------------------------ sending rules
    def _mail_sync_can_send(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        if not (self.active and self.send_enabled and self.state in ("connected", "draft")):
            return False
        if self.kind == "personal":
            return self.owner_user_id == user
        return not self.send_user_ids or user in self.send_user_ids

    @api.model
    def _mail_sync_sendable(self, user=None):
        user = user or self.env.user
        # sudo: users may send through mailboxes they cannot administrate
        mailboxes = self.sudo().search([("send_enabled", "=", True), ("active", "=", True)])
        return mailboxes.filtered(lambda m: m._mail_sync_can_send(user))

    @api.model
    def _mail_sync_default_for(self, model=None, user=None, include_personal=True):
        """Mailbox to send from by default: model rule, then the user's choice, then own mailbox."""
        user = user or self.env.user
        sendable = self._mail_sync_sendable(user)
        if model:
            for mailbox in sendable:
                if model in mailbox.send_default_model_ids.mapped("model"):
                    return mailbox
        preferred = user.sudo().mail_sync_send_mailbox_id
        if preferred and preferred in sendable:
            return preferred
        if include_personal:
            return sendable.filtered(lambda m: m.kind == "personal")[:1]
        return self.browse()

    @api.model
    def _mail_sync_from_email(self, email):
        normalized = email_normalize(email or "")
        if not normalized:
            return self.browse()
        # sudo: lookup only, called while rendering notifications for any user
        return self.sudo().search([("upn", "=", normalized), ("send_enabled", "=", True)], limit=1)

    def _mail_sync_from_address(self, user=None):
        """Formatted From address when ``user`` sends through this mailbox."""
        self.ensure_one()
        user = user or self.env.user
        if self.kind == "personal":
            return formataddr((user.name or "", self.upn))
        name = self.sender_name or self.display_name or self.upn
        if self.sender_name_mode == "user_via":
            name = f"{user.name} | {name}"
        return formataddr((name, self.upn))

    def _mail_sync_render_signature(self, user):
        self.ensure_one()
        if self.kind != "shared":
            return user.sudo().signature or ""  # sudo: reading a colleague's signature for an outgoing mail
        template = self.signature_id or self.company_id.mail_sync_shared_signature_id
        if not template:
            return user.sudo().signature or ""
        return template._render_for(user, self)

    def action_send_test_mail(self):
        """Send a test mail from this mailbox to the current user."""
        self.ensure_one()
        if not self.send_enabled:
            raise UserError(self.env._("Der Versand ist für dieses Postfach nicht aktiviert."))
        if not self.env.user.email:
            raise UserError(self.env._("Ihr Benutzer hat keine E-Mail-Adresse."))
        signature = self._mail_sync_render_signature(self.env.user)
        body = Markup("<p>%s</p>%s") % (
            self.env._("Testmail aus Odoo über das Postfach %s.", self.upn),
            signature or "",
        )
        mail = (
            self.env["mail.mail"]
            .sudo()  # sudo: a test mail on behalf of the mailbox manager
            .create(
                {
                    "subject": self.env._("Odoo Mail Sync: Testmail über %s", self.upn),
                    "email_from": self._mail_sync_from_address(self.env.user),
                    "email_to": self.env.user.email_formatted,
                    "body_html": body,
                    "mail_server_id": self.mail_server_id.id,
                    "auto_delete": False,
                }
            )
        )
        mail.send(raise_exception=True)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("Testmail an %s gesendet.", self.env.user.email),
                "sticky": False,
            },
        }
