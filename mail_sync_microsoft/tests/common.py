# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from unittest import mock

from odoo.tests.common import TransactionCase

from ..models.mail_sync_account import MailSyncAccount
from ..models.mail_sync_mailbox import MailSyncMailbox
from .fake_graph import FakeGraph

INTERNAL = "kneissl-messtechnik.de"
INFO = f"info@{INTERNAL}"


class MailSyncCase(TransactionCase):
    """Account + mailbox wired to an in-memory FakeGraph; partners Musterwerk/Max/Erika."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.graph = FakeGraph(domains=(INTERNAL,))
        cls._patchers = [
            mock.patch.object(MailSyncAccount, "_graph", lambda self: cls.graph),
            mock.patch.object(MailSyncMailbox, "_graph", lambda self: cls.graph),
        ]
        for patcher in cls._patchers:
            patcher.start()
        cls.env = cls.env(context=dict(cls.env.context, queue_job__no_delay=True))
        cls.account = cls.env["mail.sync.account"].create(
            {
                "name": "Test",
                "tenant_id": "tenant",
                "client_id": "client",
                "internal_domains": INTERNAL,
                "auth_mode": "app",
            }
        )
        cls.mailbox = cls.env["mail.sync.mailbox"].create(
            {
                "account_id": cls.account.id,
                "upn": INFO,
                "kind": "shared",
                "owner_user_id": cls.env.ref("base.user_admin").id,
                "blocklist_domains": "spam.io",
                "start_date": "2026-01-01",
            }
        )
        cls.graph.add_mailbox(INFO)
        Partner = cls.env["res.partner"]
        cls.musterwerk = Partner.create(
            {
                "name": "Musterwerk GmbH",
                "is_company": True,
                "email": "info@musterwerk.de",
                "website": "https://musterwerk.de",
            }
        )
        cls.max = Partner.create({"name": "Max Muster", "email": "max@musterwerk.de", "parent_id": cls.musterwerk.id})
        cls.erika = Partner.create({"name": "Erika Solo", "email": "erika@solo.example"})

    def setUp(self):
        super().setUp()
        # fresh in-memory mailbox per test; the patched _graph lambdas read cls.graph at call time
        type(self).graph = FakeGraph(domains=(INTERNAL,))
        self.graph.add_mailbox(INFO)
        self.mailbox.folder_ids.unlink()

    @classmethod
    def tearDownClass(cls):
        for patcher in cls._patchers:
            patcher.stop()
        super().tearDownClass()

    # helpers
    def discover(self):
        self.mailbox.job_discover_folders()
        return {f.well_known_name or f.display_name: f for f in self.mailbox.folder_ids}

    def sync_folder(self, well_known="inbox"):
        folder = self.mailbox.folder_ids.filtered(lambda f: f.well_known_name == well_known)
        self.mailbox.job_delta(folder.id)
        return folder

    def row(self, graph_id):
        return self.env["mail.sync.message"].search([("mailbox_id", "=", self.mailbox.id), ("graph_id", "=", graph_id)])
