# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class MailSyncRun(models.Model):
    _name = "mail.sync.run"
    _description = "Mail Sync: Lauf"
    _order = "id desc"

    mailbox_id = fields.Many2one("mail.sync.mailbox", required=True, ondelete="cascade", index=True)
    folder_id = fields.Many2one("mail.sync.folder", ondelete="set null")
    kind = fields.Selection(
        [("discover", "Ordner"), ("backfill", "Backfill"), ("delta", "Delta"), ("ingest", "Import")], required=True
    )
    state = fields.Selection([("running", "Läuft"), ("done", "Fertig"), ("error", "Fehler")], default="running")
    date_start = fields.Datetime(default=fields.Datetime.now, readonly=True)
    date_end = fields.Datetime(readonly=True)
    fetched = fields.Integer()
    created = fields.Integer()
    linked = fields.Integer()
    skipped = fields.Integer()
    errors = fields.Integer()
    error = fields.Text()

    def bump(self, field, n=1):
        self.ensure_one()
        self[field] = self[field] + n

    def finish(self, error=None):
        self.write({"state": "error" if error else "done", "date_end": fields.Datetime.now(), "error": error or False})
