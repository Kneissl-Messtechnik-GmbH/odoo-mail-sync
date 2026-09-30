# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo.tests.common import BaseCase

from ..services import prefilter as pf

INTERNAL = ["kneissl-messtechnik.de", "chotest.de"]


class TestPrefilter(BaseCase):
    def test_normalize_and_domain(self):
        self.assertEqual(pf.normalize("Max Muster <Max@Foo.DE>"), "max@foo.de")
        self.assertEqual(pf.split_domain("a@Foo.DE"), "foo.de")
        self.assertEqual(pf.normalize("kein mail"), "")

    def test_is_internal(self):
        self.assertTrue(pf.is_internal(["a@kneissl-messtechnik.de", "B@Chotest.de"], INTERNAL))
        self.assertFalse(pf.is_internal(["a@kneissl-messtechnik.de", "kunde@firma.de"], INTERNAL))
        self.assertFalse(pf.is_internal([], INTERNAL))
        self.assertFalse(pf.is_internal(["a@kneissl-messtechnik.de"], []))

    def test_external_addresses(self):
        result = pf.external_addresses(
            ["me@kneissl-messtechnik.de", "Kunde@Firma.de", "kunde@firma.de", "x@other.com"], INTERNAL
        )
        self.assertEqual(result, ["kunde@firma.de", "x@other.com"])

    def test_is_blocked(self):
        self.assertTrue(pf.is_blocked(["news@spam.io"], [], ["spam.io"]))
        self.assertTrue(pf.is_blocked(["Bob@firma.de"], ["bob@firma.de"], []))
        self.assertFalse(pf.is_blocked(["bob@firma.de"], ["alice@firma.de"], ["spam.io"]))

    def test_is_freemail(self):
        self.assertTrue(pf.is_freemail("x@gmail.com"))
        self.assertTrue(pf.is_freemail("x@web.de"))
        self.assertFalse(pf.is_freemail("x@musterwerk.de"))

    def test_is_automatic(self):
        self.assertTrue(pf.is_automatic({"Auto-Submitted": "auto-replied"}))
        self.assertTrue(pf.is_automatic({"X-Auto-Response-Suppress": "All"}))
        self.assertTrue(pf.is_automatic({"Precedence": "bulk"}))
        self.assertTrue(pf.is_automatic({}, attachments=[{"name": "invite.ics"}]))
        self.assertTrue(pf.is_automatic({}, content_type="multipart/report; report-type=delivery-status"))
        self.assertFalse(pf.is_automatic({"Auto-Submitted": "no"}, attachments=[{"name": "angebot.pdf"}]))

    def test_is_private(self):
        self.assertTrue(pf.is_private("[PRIVAT] Arzttermin", "Inbox", ["[PRIVAT]"]))
        self.assertTrue(pf.is_private("Hallo", "Privat", ["[PRIVAT]"]))
        self.assertFalse(pf.is_private("Angebot", "Inbox", ["[PRIVAT]"]))
