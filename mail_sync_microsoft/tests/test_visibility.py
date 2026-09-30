# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo.exceptions import AccessError

from .common import INFO, MailSyncCase


class TestVisibility(MailSyncCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group_user = cls.env.ref("mail_sync_microsoft.group_mail_sync_user")
        cls.owner = cls.env["res.users"].create(
            {"name": "Owner", "login": "owner@test", "email": "owner@test", "groups_id": [(6, 0, [group_user.id])]}
        )
        cls.other = cls.env["res.users"].create(
            {"name": "Other", "login": "other@test", "email": "other@test", "groups_id": [(6, 0, [group_user.id])]}
        )
        cls.mailbox.write({"owner_user_id": cls.owner.id, "visibility_default": "private"})

    def test_private_rows_only_for_owner(self):
        self.graph.add_message(INFO, "inbox", "m1", "Privat", "max@musterwerk.de", [INFO])
        self.discover()
        self.sync_folder("inbox")
        row = self.row("m1")
        Message = self.env["mail.sync.message"]
        self.assertTrue(Message.with_user(self.owner).search([("id", "=", row.id)]))
        self.assertFalse(Message.with_user(self.other).search([("id", "=", row.id)]))
        with self.assertRaises(AccessError):
            row.with_user(self.other).read(["subject"])
        row.with_user(self.owner).action_share()
        self.assertTrue(Message.with_user(self.other).search([("id", "=", row.id)]))

    def test_other_user_cannot_share(self):
        self.graph.add_message(INFO, "inbox", "m1", "Privat", "max@musterwerk.de", [INFO])
        self.discover()
        self.sync_folder("inbox")
        row = self.row("m1")
        with self.assertRaises(Exception):  # noqa: B017 - AccessError or UserError depending on rule evaluation order
            row.with_user(self.other).action_share()
