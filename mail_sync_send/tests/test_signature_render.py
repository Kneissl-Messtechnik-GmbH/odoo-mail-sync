# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from markupsafe import Markup
from odoo.tests.common import BaseCase, tagged

from ..services import signature


@tagged("post_install", "-at_install")
class TestSignatureRender(BaseCase):
    def test_placeholders_are_escaped(self):
        out = signature.render("<p>{{name}}</p>", {"name": "<script>alert(1)</script> & Co"})
        self.assertIn("&lt;script&gt;", out)
        self.assertNotIn("<script>", out)

    def test_empty_line_disappears_with_label(self):
        tpl = '<div><p>Telefon: <a href="tel:{{phone_link}}">{{phone}}</a></p><p>Mobil: <a href="tel:{{mobile_link}}">{{mobile}}</a></p></div>'
        out = signature.render(tpl, {"phone": "+49 1", "phone_link": "+491"})
        self.assertIn("Telefon:", out)
        self.assertIn('href="tel:+491"', out)
        self.assertNotIn("Mobil", out)

    def test_image_without_source_is_removed_even_with_alt(self):
        out = signature.render('<p>A<img src="{{photo_url}}" alt="{{name}}"/>B</p>', {"name": "Max"})
        self.assertNotIn("<img", out)
        self.assertIn("AB", out)

    def test_image_with_source_kept(self):
        out = signature.render(
            '<img src="{{photo_url}}" alt="{{name}}">', {"name": "Max", "photo_url": "https://x/1.png"}
        )
        self.assertIn('src="https://x/1.png"', out)

    def test_partial_line_kept(self):
        out = signature.render("<p>{{name}} · {{function}}</p>", {"name": "Max"})
        self.assertIn("Max ·", out)

    def test_extra_is_raw_html(self):
        out = signature.render("<div>{{extra}}</div>", {"extra": Markup("<b>Urlaub</b>")})
        self.assertIn("<b>Urlaub</b>", out)

    def test_table_cells_are_kept(self):
        out = signature.render("<table><tr><td>{{photo_url}}</td><td>{{name}}</td></tr></table>", {"name": "Max"})
        self.assertEqual(out.count("<td"), 2)

    def test_tel_link(self):
        self.assertEqual(signature.tel_link("+49 (0)7152 1234-5"), "+49715212345")
        self.assertEqual(signature.tel_link(False), "")

    def test_rewrite_image_sources(self):
        html = '<p><img src="/web/image/42-abc/x.png?access_token=t"><img src="{{photo_url}}"></p>'
        out = signature.rewrite_image_sources(html, lambda src: f"https://pub/{signature.attachment_id_from_src(src)}")
        self.assertIn('src="https://pub/42"', out)
        self.assertIn('src="{{photo_url}}"', out)

    def test_percent_encoded_placeholders_from_sanitizer(self):
        # newer lxml/sanitizer versions store href/src placeholders percent-encoded
        out = signature.render(
            '<p><a href="tel:%7B%7Bphone_link%7D%7D">{{phone}}</a><img src="%7B%7Bphoto_url%7D%7D"></p>',
            {"phone": "+49 1", "phone_link": "+491", "photo_url": "https://x/1.png"},
        )
        self.assertIn('href="tel:+491"', out)
        self.assertIn('src="https://x/1.png"', out)
        self.assertNotIn("%7B", out)
