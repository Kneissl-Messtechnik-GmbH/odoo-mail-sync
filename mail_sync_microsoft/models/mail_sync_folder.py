# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, fields, models

from ..services.graph import EXCLUDED_WELL_KNOWN


class MailSyncFolder(models.Model):
    _name = "mail.sync.folder"
    _description = "Mail Sync: Ordner"
    _order = "mailbox_id, path"

    mailbox_id = fields.Many2one("mail.sync.mailbox", required=True, ondelete="cascade", index=True)
    graph_id = fields.Char(required=True, index=True)
    display_name = fields.Char(required=True)
    path = fields.Char()
    well_known_name = fields.Char(index=True)
    include = fields.Boolean(string="Synchronisieren", default=True)
    delta_link = fields.Text(copy=False)
    needs_full_resync = fields.Boolean(default=False)
    backfill_done = fields.Boolean(default=False)
    last_sync = fields.Datetime(readonly=True)
    message_count = fields.Integer(compute="_compute_message_count")

    _sql_constraints = [("graph_id_unique", "unique(mailbox_id, graph_id)", "Ordner bereits vorhanden.")]

    def _compute_message_count(self):
        counts = dict(
            self.env["mail.sync.message"]._read_group([("folder_id", "in", self.ids)], ["folder_id"], ["__count"])
        )
        for folder in self:
            folder.message_count = counts.get(folder, 0)

    @api.model
    def _default_include(self, well_known_name, display_name, mailbox, path=None, parent_included=True):
        well_known = (well_known_name or "").lower()
        if not parent_included or well_known in EXCLUDED_WELL_KNOWN:
            return False
        if well_known == "sentitems" and not mailbox.sync_sent:
            return False
        haystack = (path or display_name or "").strip().lower()
        return not any(pattern in haystack for pattern in mailbox.account_id._excluded_folder_patterns())
