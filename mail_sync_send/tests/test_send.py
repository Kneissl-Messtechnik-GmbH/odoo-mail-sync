# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from email.message import EmailMessage
from unittest import mock

from odoo.addons.mail_sync_microsoft.models.mail_sync_mailbox import MailSyncMailbox
from odoo.addons.mail_sync_microsoft.tests.common import INFO, MailSyncCase
from odoo.tests.common import new_test_user, tagged

from ..services.graph_session import GraphSendSession
from .test_graph_session import SpyGraph


@tagged("post_install", "-at_install")
class TestSend(MailSyncCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sales = new_test_user(
            cls.env,
            login="sales_ms",
            name="Sabine Sales",
            email="s.sales@kneissl-messtechnik.de",
            groups="base.group_user",
        )
        cls.mailbox.write(
            {
                "send_enabled": True,
                "sender_name": "Kneissl Info",
                "send_default_model_ids": [(6, 0, cls.env.ref("base.model_res_partner").ids)],
                "state": "connected",
            }
        )

    def test_mail_server_follows_mailbox(self):
        server = self.mailbox.mail_server_id
        self.assertTrue(server)
        self.assertEqual(server.smtp_authentication, "mail_sync_graph")
        self.assertEqual(server.from_filter, INFO)
        self.mailbox.send_enabled = False
        self.assertFalse(server.active)
        self.mailbox.send_enabled = True
        self.assertTrue(server.active)
        self.assertEqual(self.mailbox.mail_server_id, server)

    def test_connect_returns_graph_session_and_sends(self):
        spy = SpyGraph()
        with mock.patch.object(MailSyncMailbox, "_graph", lambda self: spy):
            Server = self.env["ir.mail_server"].with_context(mail_sync_graph_force_session=True)
            session = Server.connect(smtp_from=INFO)
            self.assertIsInstance(session, GraphSendSession)
            msg = EmailMessage()
            msg["From"] = f"Kneissl Info <{INFO}>"
            msg["To"] = "max@musterwerk.de"
            msg["Subject"] = "Test"
            msg.set_content("x")
            session.send_message(msg, INFO, ["max@musterwerk.de"])
        self.assertEqual(spy.sent[0][0], INFO)

    def test_other_sender_uses_normal_smtp(self):
        Server = self.env["ir.mail_server"].with_context(mail_sync_graph_force_session=True)
        # even when the graph server is the only one, a foreign sender never falls back to it
        self.assertIsNone(Server._mail_sync_resolve(None, "someone@else.de", None, False))
        server, _from = Server._find_mail_server("someone@else.de")
        self.assertNotEqual(server, self.mailbox.mail_server_id)
        server, _from = Server._find_mail_server(f"Kneissl Info <{INFO}>")
        self.assertEqual(server, self.mailbox.mail_server_id)

    def test_permissions(self):
        self.assertTrue(self.mailbox._mail_sync_can_send(self.sales))
        other = new_test_user(self.env, login="other_ms", groups="base.group_user")
        self.mailbox.send_user_ids = [(6, 0, other.ids)]
        self.assertFalse(self.mailbox._mail_sync_can_send(self.sales))

    def test_from_address_modes(self):
        self.assertIn(f"Kneissl Info <{INFO}>", self.mailbox._mail_sync_from_address(self.sales).replace('"', ""))
        self.mailbox.sender_name_mode = "user_via"
        self.assertIn("Sabine Sales | Kneissl Info", self.mailbox._mail_sync_from_address(self.sales))

    def _post_as_sales(self, **kwargs):
        # like the chatter controller: sudo() for the access check, env.user stays the real user
        return self.max.with_user(self.sales).sudo().message_post(**kwargs)

    def test_chatter_post_uses_model_default_mailbox(self):
        message = self._post_as_sales(body="Hallo", message_type="comment", subtype_xmlid="mail.mt_comment")
        self.assertIn(INFO, message.email_from)
        self.assertEqual(message.author_id, self.sales.partner_id)

    def test_note_is_not_touched(self):
        message = self._post_as_sales(body="intern", message_type="comment", subtype_xmlid="mail.mt_note")
        self.assertNotIn(INFO, message.email_from or "")

    def test_composer_default_and_from(self):
        composer = (
            self.env["mail.compose.message"]
            .with_user(self.sales)
            .with_context(default_model="res.partner", default_res_ids=self.max.ids, default_composition_mode="comment")
            .create({"body": "Hallo", "subject": "Angebot"})
        )
        self.assertEqual(composer.mail_sync_send_mailbox_id, self.mailbox)
        self.assertIn(INFO, composer.email_from)
        self.assertEqual(composer.author_id, self.sales.partner_id)

    def test_shared_signature_in_notification(self):
        template = self.env["mail.sync.signature"].create(
            {
                "name": "Shared",
                "usage": "shared",
                "body_html": "<p>{{mailbox_name}}</p><p>Ansprechpartner: {{name}}</p>",
            }
        )
        self.mailbox.signature_id = template
        message = self._post_as_sales(body="Hallo", message_type="comment", subtype_xmlid="mail.mt_comment")
        context = self.max._notify_by_email_prepare_rendering_context(message, msg_vals={})
        self.assertIn("Ansprechpartner: Sabine Sales", context["signature"])
        self.assertIn("Kneissl Info", context["signature"])
