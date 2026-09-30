# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64
import hashlib
import logging
from datetime import timedelta

import psycopg2.errors
from cryptography.fernet import Fernet, InvalidToken
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services import graph as graph_service
from ..services import sync as sync_service
from .mail_sync_account import _lines

_logger = logging.getLogger(__name__)

CHANNEL = "root.mail_sync"


class MailSyncMailbox(models.Model):
    _name = "mail.sync.mailbox"
    _description = "Mail Sync: Postfach"
    _inherit = ["mail.thread"]
    _order = "upn"

    name = fields.Char(compute="_compute_name", store=True)
    account_id = fields.Many2one("mail.sync.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    auth_mode = fields.Selection(related="account_id.auth_mode")
    upn = fields.Char(string="Postfach (UPN / E-Mail)", required=True, tracking=True)
    display_name = fields.Char(string="Anzeigename")
    kind = fields.Selection(
        [("personal", "Persönliches Postfach"), ("shared", "Funktionspostfach")], required=True, default="personal"
    )
    owner_user_id = fields.Many2one(
        "res.users",
        string="Eigentümer",
        tracking=True,
        help="Sieht private Mails dieses Postfachs und verbindet es im delegierten Modus.",
    )
    active = fields.Boolean(default=True, tracking=True)
    visibility_default = fields.Selection(
        [("shared", "Geteilt (im Chatter sichtbar)"), ("private", "Privat (nur Eigentümer, bis freigegeben)")],
        required=True,
        default="shared",
        tracking=True,
    )
    start_date = fields.Date(
        string="Synchronisieren ab",
        default=lambda self: fields.Date.context_today(self) - timedelta(days=90),
        required=True,
    )
    sync_sent = fields.Boolean(string="Gesendete Elemente einbeziehen", default=True)
    auto_create_partner = fields.Selection(
        [("no", "Nie"), ("company", "Nur unter bekannter Firma"), ("always", "Immer")],
        string="Kontakte automatisch anlegen",
        default="company",
        required=True,
    )
    blocklist_addresses = fields.Text(string="Blockliste Adressen", help="Eine je Zeile.")
    blocklist_domains = fields.Text(string="Blockliste Domains", help="Eine je Zeile.")
    exclude_subject_prefixes = fields.Char(string="Private Betreffpräfixe", default="[PRIVAT]")
    state = fields.Selection(
        [
            ("draft", "Nicht verbunden"),
            ("connected", "Verbunden"),
            ("auth_error", "Anmeldung nötig"),
            ("error", "Fehler"),
        ],
        default="draft",
        tracking=True,
    )
    last_sync = fields.Datetime(compute="_compute_last_sync")
    last_error = fields.Text(readonly=True)
    refresh_token_enc = fields.Char(string="Refresh-Token (verschlüsselt)", groups="base.group_system", copy=False)
    folder_ids = fields.One2many("mail.sync.folder", "mailbox_id", string="Ordner")
    sync_message_ids = fields.One2many("mail.sync.message", "mailbox_id", string="Nachrichten")
    message_count = fields.Integer(compute="_compute_counts")
    linked_count = fields.Integer(compute="_compute_counts")
    unmatched_count = fields.Integer(compute="_compute_counts")

    _sql_constraints = [("upn_account_unique", "unique(account_id, upn)", "Dieses Postfach ist bereits angelegt.")]

    @api.depends("upn", "display_name")
    def _compute_name(self):
        for mailbox in self:
            mailbox.name = mailbox.display_name or mailbox.upn or ""

    @api.depends("folder_ids.last_sync")
    def _compute_last_sync(self):
        for mailbox in self:
            dates = [d for d in mailbox.folder_ids.mapped("last_sync") if d]
            mailbox.last_sync = max(dates) if dates else False

    def _compute_counts(self):
        Message = self.env["mail.sync.message"]
        for mailbox in self:
            base = [("mailbox_id", "=", mailbox.id)]
            mailbox.message_count = Message.search_count(base)
            mailbox.linked_count = Message.search_count(base + [("state", "=", "linked")])
            mailbox.unmatched_count = Message.search_count(base + [("state", "in", ("unmatched", "matched"))])

    @api.constrains("auth_mode", "owner_user_id", "kind")
    def _check_owner(self):
        for mailbox in self:
            if mailbox.auth_mode == "delegated" and not mailbox.owner_user_id:
                raise ValidationError(self.env._("Im delegierten Modus braucht jedes Postfach einen Eigentümer."))

    @api.onchange("upn")
    def _onchange_upn(self):
        if self.upn:
            self.upn = self.upn.strip().lower()
            user = self.env["res.users"].search([("email", "=ilike", self.upn)], limit=1)
            if user and not self.owner_user_id:
                self.owner_user_id = user

    # ------------------------------------------------------------ helpers
    def _blocklist(self):
        return _lines(self.blocklist_addresses), _lines(self.blocklist_domains)

    def _subject_prefixes(self):
        return [p.strip() for p in (self.exclude_subject_prefixes or "").split(",") if p.strip()]

    # token encryption (delegated mode) -----------------------------------
    def _fernet(self):
        secret = self.env["ir.config_parameter"].sudo().get_param("database.secret")  # sudo: key derivation only
        key = base64.urlsafe_b64encode(hashlib.sha256(f"mail_sync:{secret}".encode()).digest())
        return Fernet(key)

    def _store_refresh_token(self, token):
        self.sudo().write(
            {"refresh_token_enc": self._fernet().encrypt(token.encode()).decode()}
        )  # sudo: field is system-only

    def _load_refresh_token(self):
        enc = self.sudo().refresh_token_enc  # sudo: field is system-only, read by the sync job
        if not enc:
            return ""
        try:
            return self._fernet().decrypt(enc.encode()).decode()
        except InvalidToken:
            return ""

    def _graph(self):
        self.ensure_one()
        account = self.account_id
        if account.auth_mode == "app":
            return account._graph()
        refresh_token = self._load_refresh_token()
        if not refresh_token:
            raise graph_service.GraphUnauthorized("Postfach ist nicht verbunden")
        mailbox = self

        def on_refresh(new_token):
            mailbox._store_refresh_token(new_token)

        provider = graph_service.DelegatedTokenProvider(
            account.tenant_id, account.client_id, account._get_secret(), refresh_token, on_refresh=on_refresh
        )
        return account._make_graph(provider)

    # ------------------------------------------------------------ actions
    def action_connect(self):
        """Delegated mode: start the OAuth flow for this mailbox (only its owner may do this)."""
        self.ensure_one()
        if self.auth_mode != "delegated":
            raise UserError(self.env._("Diese Verbindung nutzt die zentrale Anwendungsberechtigung."))
        if self.owner_user_id != self.env.user and not self.env.user.has_group(
            "mail_sync_microsoft.group_mail_sync_manager"
        ):
            raise UserError(self.env._("Nur der Eigentümer kann dieses Postfach verbinden."))
        return {"type": "ir.actions.act_url", "url": f"/mail_sync/oauth/start?mailbox_id={self.id}", "target": "self"}

    def action_discover_folders(self):
        for mailbox in self:
            mailbox.with_delay(channel=CHANNEL, description=f"Mail Sync: Ordner {mailbox.upn}").job_discover_folders()
        return True

    def action_backfill(self):
        for mailbox in self:
            if not mailbox.folder_ids:
                mailbox.job_discover_folders()
            for folder in mailbox.folder_ids.filtered("include"):
                mailbox.with_delay(
                    channel=CHANNEL,
                    description=f"Mail Sync: Backfill {mailbox.upn}/{folder.display_name}",
                    identity_key=f"mail_sync_backfill_{folder.id}",
                    max_retries=50,
                ).job_backfill(folder.id)
        return True

    def action_sync_now(self):
        for mailbox in self:
            mailbox._enqueue_delta(force=True)
        return True

    def action_view_messages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.name,
            "res_model": "mail.sync.message",
            "view_mode": "list,form",
            "domain": [("mailbox_id", "=", self.id)],
            "context": {"search_default_group_state": 1},
        }

    def _enqueue_delta(self, force=False):
        for mailbox in self.filtered(lambda m: m.active and m.state in ("connected", "draft")):
            for folder in mailbox.folder_ids.filtered("include"):
                if not folder.backfill_done:
                    # initial load still pending: (re)queue the backfill once instead of a delta
                    mailbox.with_delay(
                        channel=CHANNEL,
                        description=f"Mail Sync: Backfill {mailbox.upn}/{folder.display_name}",
                        identity_key=f"mail_sync_backfill_{folder.id}",
                        max_retries=50,
                    ).job_backfill(folder.id)
                    continue
                mailbox.with_delay(
                    channel=CHANNEL,
                    description=f"Mail Sync: Delta {mailbox.upn}/{folder.display_name}",
                    identity_key=None if force else f"mail_sync_delta_{folder.id}",
                    max_retries=50,
                ).job_delta(folder.id)

    @api.model
    def _cron_delta(self):
        mailboxes = self.search([("active", "=", True), ("state", "in", ("connected", "draft"))])
        mailboxes._enqueue_delta()
        return True

    @api.model
    def _cron_housekeeping(self):
        self.env["mail.sync.message"]._purge_unmatched()
        for mailbox in self.search([("active", "=", True), ("state", "=", "connected")]):
            mailbox.with_delay(channel=CHANNEL, description=f"Mail Sync: Ordner {mailbox.upn}").job_discover_folders()
        return True

    # --------------------------------------------------------------- jobs
    def _run_job(self, kind, folder, func):
        """Run a sync step with error mapping into queue_job retries and mailbox state."""
        self.ensure_one()
        from odoo.addons.queue_job.exception import RetryableJobError  # noqa: PLC0415

        run = self.env["mail.sync.run"].create(
            {"mailbox_id": self.id, "folder_id": folder.id if folder else False, "kind": kind}
        )
        try:
            result = func(run)
        except graph_service.GraphThrottled as exc:
            run.finish(error=f"Throttled, retry in {exc.retry_after}s")
            raise RetryableJobError(str(exc), seconds=max(exc.retry_after, 5), ignore_retry=True) from exc
        except graph_service.GraphGone as exc:
            _logger.info(
                "Mail Sync: Delta-Status für %s/%s ungültig, Neusynchronisation",
                self.upn,
                folder and folder.display_name,
            )
            if folder:
                folder.write({"needs_full_resync": True, "delta_link": False})
            run.finish(error=f"Delta-Status ungültig, Ordner wird neu synchronisiert: {exc}")
            raise RetryableJobError(str(exc), seconds=5) from exc
        except graph_service.GraphUnauthorized as exc:
            self.write({"state": "auth_error", "last_error": str(exc)})
            run.finish(error=str(exc))
            self._notify_owner(
                self.env._("Die Anmeldung für das Postfach %s ist abgelaufen oder die Berechtigung fehlt.", self.upn)
            )
            return False
        except graph_service.GraphError as exc:
            self.write({"state": "error", "last_error": str(exc)})
            run.finish(error=str(exc))
            raise RetryableJobError(str(exc), seconds=600) from exc
        # Only touch the mailbox row when something changed: every folder job of this mailbox
        # would otherwise update the same row and long page jobs would keep failing with
        # serialization errors against the short delta jobs of the other folders.
        vals = {}
        if self.state != "connected":
            vals["state"] = "connected"
        if self.last_error:
            vals["last_error"] = False
        if vals:
            self.write(vals)
        run.finish()
        return result

    def _notify_owner(self, body):
        user = self.owner_user_id or self.env.ref("base.user_admin")
        self.activity_schedule("mail.mail_activity_data_todo", summary=body[:100], note=body, user_id=user.id)

    def job_discover_folders(self):
        self.ensure_one()
        return self._run_job("discover", None, lambda run: sync_service.discover_folders(self, run))

    def _lock_folder(self, folder):
        """Serialise backfill/delta jobs per folder (they share ``folder.delta_link`` as progress).

        Two jobs on the same folder would fetch the same Graph page and collide on the
        registry's unique key. The row lock is held until the job's transaction commits;
        a concurrent job retries later instead of failing.
        """
        from odoo.addons.queue_job.exception import RetryableJobError  # noqa: PLC0415

        message = f"Ordner {folder.display_name} wird gerade synchronisiert"
        try:
            # savepoint: a failed NOWAIT would otherwise abort the whole job transaction and
            # queue_job could not even postpone the job; the lock outlives the released savepoint
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute("SELECT id FROM mail_sync_folder WHERE id = %s FOR UPDATE NOWAIT", (folder.id,))
        except psycopg2.errors.LockNotAvailable as exc:
            raise RetryableJobError(message, seconds=60, ignore_retry=True) from exc

    def _job_page(self, kind, folder):
        """One Graph page per job; re-enqueue itself while the round has more pages."""
        self._lock_folder(folder)
        result = {}

        def step(run):
            result["more"] = sync_service.sync_page(self, folder, run)
            return f"{run.fetched} Nachrichten"

        summary = self._run_job(kind, folder, step)
        if result.get("more"):
            self.with_delay(
                channel=CHANNEL,
                description=f"Mail Sync: {kind} {self.upn}/{folder.display_name} (Fortsetzung)",
                identity_key=f"mail_sync_{kind}_{folder.id}",
                max_retries=50,
            ).job_backfill(folder.id) if kind == "backfill" else self.with_delay(
                channel=CHANNEL,
                description=f"Mail Sync: {kind} {self.upn}/{folder.display_name} (Fortsetzung)",
                identity_key=f"mail_sync_{kind}_{folder.id}",
                max_retries=50,
            ).job_delta(folder.id)
        return summary

    def job_backfill(self, folder_id, page=0):
        self.ensure_one()
        folder = self.env["mail.sync.folder"].browse(folder_id).exists()
        if not folder:
            return "Ordner gelöscht"
        return self._job_page("backfill", folder)

    def job_delta(self, folder_id):
        self.ensure_one()
        folder = self.env["mail.sync.folder"].browse(folder_id).exists()
        if not folder or not folder.include:
            return "Ordner ausgeschlossen"
        return self._job_page("delta", folder)

    def job_ingest_message(self, message_id):
        self.ensure_one()
        message = self.env["mail.sync.message"].browse(message_id).exists()
        if not message:
            return "gelöscht"
        return self._run_job("ingest", message.folder_id, lambda run: sync_service.ingest_registry_row(message, run))
