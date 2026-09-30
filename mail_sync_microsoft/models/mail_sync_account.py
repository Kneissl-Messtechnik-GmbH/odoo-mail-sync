# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging
import os

from odoo import api, fields, models
from odoo.exceptions import UserError

from ..services import graph as graph_service
from ..services.prefilter import DEFAULT_FREEMAIL_DOMAINS

_logger = logging.getLogger(__name__)


def _lines(text):
    """Split a comma/newline separated text field into a clean lower-cased list."""
    if not text:
        return []
    raw = text.replace(",", "\n").replace(";", "\n").splitlines()
    return [x.strip().lower() for x in raw if x.strip()]


class MailSyncAccount(models.Model):
    _name = "mail.sync.account"
    _description = "Mail Sync: Microsoft 365 Verbindung"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True, default="Microsoft 365")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    tenant_id = fields.Char(string="Tenant-ID", required=True, tracking=True)
    client_id = fields.Char(string="Client-ID (App-ID)", required=True, tracking=True)
    auth_mode = fields.Selection(
        [("app", "Zentral (Anwendungsberechtigung, Exchange-Freigabe)"), ("delegated", "Je Benutzer (delegiert)")],
        required=True,
        default="app",
        tracking=True,
    )
    credential_source = fields.Selection(
        [
            ("env", "Umgebungsvariable MAIL_SYNC_CLIENT_SECRET"),
            ("param", "Systemparameter (verschlüsselt gespeichert)"),
        ],
        required=True,
        default="env",
    )
    secret_param_key = fields.Char(compute="_compute_secret_param_key")
    secret_configured = fields.Boolean(compute="_compute_secret_configured")
    internal_domains = fields.Text(
        string="Eigene Domains",
        help="Eine je Zeile. Mails, deren Adressen alle zu diesen Domains gehören, werden nicht gespiegelt. "
        "Wird beim Verbindungstest aus Microsoft 365 vorbelegt.",
    )
    freemail_domains = fields.Text(
        string="Freemail-Domains",
        default="\n".join(DEFAULT_FREEMAIL_DOMAINS),
        help="Domains, die nie als Firmen-Domain gewertet werden.",
    )
    retention_days = fields.Integer(
        string="Aufbewahrung nicht zugeordneter Mails (Tage)",
        default=30,
        help="Registereinträge ohne Zuordnung werden nach dieser Frist gelöscht; die Mail bleibt in Microsoft 365.",
    )
    attachment_max_mb = fields.Integer(string="Anhänge bis (MB)", default=10)
    state = fields.Selection(
        [("draft", "Nicht getestet"), ("connected", "Verbunden"), ("error", "Fehler")], default="draft", tracking=True
    )
    last_error = fields.Text(readonly=True)
    mailbox_ids = fields.One2many("mail.sync.mailbox", "account_id", string="Postfächer")
    mailbox_count = fields.Integer(compute="_compute_mailbox_count")

    @api.depends("mailbox_ids")
    def _compute_mailbox_count(self):
        for account in self:
            account.mailbox_count = len(account.mailbox_ids)

    def _compute_secret_param_key(self):
        for account in self:
            account.secret_param_key = f"mail_sync.client_secret.{account.id or 'new'}"

    def _compute_secret_configured(self):
        for account in self:
            account.secret_configured = bool(account._get_secret(raise_if_missing=False))

    # ------------------------------------------------------------- helpers
    def _internal_domain_list(self):
        return _lines(self.internal_domains)

    def _freemail_domain_list(self):
        return _lines(self.freemail_domains) or list(DEFAULT_FREEMAIL_DOMAINS)

    def _get_secret(self, raise_if_missing=True):
        self.ensure_one()
        if self.credential_source == "env":
            secret = os.environ.get("MAIL_SYNC_CLIENT_SECRET") or os.environ.get(f"MAIL_SYNC_CLIENT_SECRET_{self.id}")
        else:
            # sudo: the secret parameter is restricted to administrators; the sync jobs run as a service user.
            secret = self.env["ir.config_parameter"].sudo().get_param(self.secret_param_key)
        if not secret and raise_if_missing:
            raise UserError(self.env._("Für die Verbindung %s ist kein Client-Secret hinterlegt.", self.name))
        return secret or ""

    def action_store_secret(self):
        """Opens the parameter form so an administrator can store the secret (param mode)."""
        self.ensure_one()
        param = self.env["ir.config_parameter"].sudo().search([("key", "=", self.secret_param_key)], limit=1)
        if not param:
            param = self.env["ir.config_parameter"].sudo().create({"key": self.secret_param_key, "value": ""})
        return {
            "type": "ir.actions.act_window",
            "res_model": "ir.config_parameter",
            "res_id": param.id,
            "view_mode": "form",
            "target": "new",
        }

    def _token_provider(self):
        self.ensure_one()
        return graph_service.AppTokenProvider(self.tenant_id, self.client_id, client_secret=self._get_secret())

    def _make_graph(self, token_provider):
        """Factory for the Graph client. Tests replace this method with a fake."""
        return graph_service.GraphClient(token_provider)

    def _graph(self):
        self.ensure_one()
        return self._make_graph(self._token_provider())

    # ------------------------------------------------------------- actions
    def action_test_connection(self):
        for account in self:
            try:
                domains = account._graph().verified_domains()
            except graph_service.GraphError as exc:
                account.write({"state": "error", "last_error": str(exc)})
                raise UserError(self.env._("Verbindung fehlgeschlagen: %s", exc)) from exc
            vals = {"state": "connected", "last_error": False}
            if domains and not account.internal_domains:
                vals["internal_domains"] = "\n".join(sorted(domains))
            account.write(vals)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "message": self.env._("Verbindung zu Microsoft 365 erfolgreich."),
                "sticky": False,
            },
        }

    def action_view_mailboxes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Postfächer"),
            "res_model": "mail.sync.mailbox",
            "view_mode": "list,form",
            "domain": [("account_id", "=", self.id)],
            "context": {"default_account_id": self.id},
        }
