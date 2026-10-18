# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from email import message_from_bytes
from email.message import EmailMessage

from odoo.tests.common import BaseCase, tagged

from ..services.graph_session import GraphSendSession


class SpyGraph:
    def __init__(self):
        self.sent = []

    def send_mime(self, upn, mime_bytes):
        self.sent.append((upn, mime_bytes))
        return True


@tagged("post_install", "-at_install")
class TestGraphSession(BaseCase):
    def _message(self):
        msg = EmailMessage()
        msg["From"] = "Kundenservice <kundenservice@firma.de>"
        msg["To"] = "Max <max@kunde.de>"
        msg["Return-Path"] = "bounce@firma.de"
        msg["Subject"] = "Angebot"
        msg.set_content("Hallo")
        return msg

    def test_envelope_only_recipients_become_bcc(self):
        graph = SpyGraph()
        session = GraphSendSession(graph, "kundenservice@firma.de", from_filter="kundenservice@firma.de")
        session.send_message(self._message(), "bounce@firma.de", ["max@kunde.de", "chef@firma.de"])
        upn, raw = graph.sent[0]
        sent = message_from_bytes(raw)
        self.assertEqual(upn, "kundenservice@firma.de")
        self.assertEqual(sent["Bcc"], "chef@firma.de")
        self.assertIsNone(sent["Return-Path"])
        self.assertEqual(session.from_filter, "kundenservice@firma.de")

    def test_no_bcc_when_all_visible(self):
        graph = SpyGraph()
        GraphSendSession(graph, "a@firma.de").send_message(self._message(), "a@firma.de", ["MAX@kunde.de"])
        self.assertIsNone(message_from_bytes(graph.sent[0][1])["Bcc"])

    def test_quit_is_noop(self):
        self.assertIsNone(GraphSendSession(SpyGraph(), "a@firma.de").quit())
