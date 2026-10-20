# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64

from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase, new_test_user, tagged

# 1x1 PNG
PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
).decode()


@tagged("post_install", "-at_install")
class TestSignature(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param("mail_sync.signature_base_url", "https://odoo.example")
        cls.template = cls.env["mail.sync.signature"].create(
            {
                "name": "Personal",
                "usage": "personal",
                "body_html": '<p>{{name}}</p><p>Mobil: {{mobile}}</p><img src="{{photo_url}}"/><div>{{extra}}</div>',
            }
        )
        cls.user = new_test_user(cls.env, login="sig_user", name="Paula Person", groups="base.group_user")

    def test_template_applied_to_user_signature(self):
        self.user.mail_sync_signature_id = self.template
        self.assertIn("Paula Person", self.user.signature)
        self.assertNotIn("Mobil", self.user.signature)

    def test_partner_change_refreshes_signature(self):
        self.user.mail_sync_signature_id = self.template
        self.user.partner_id.mobile = "+49 170 1"
        self.assertIn("Mobil: +49 170 1", self.user.signature)

    def test_photo_is_public_absolute(self):
        self.user.image_1920 = PNG
        self.user.mail_sync_signature_id = self.template
        attachment = self.user.mail_sync_photo_attachment_id
        self.assertTrue(attachment.public)
        self.assertIn(f"https://odoo.example/web/image/{attachment.id}/", self.user.signature)

    def test_unmanaged_signature_untouched(self):
        self.user.write({"mail_sync_signature_managed": False, "signature": "<p>Meine</p>"})
        self.user.mail_sync_signature_id = self.template
        self.assertIn("Meine", self.user.signature)

    def test_company_default(self):
        self.env.company.mail_sync_personal_signature_id = self.template
        self.assertIn("Paula Person", self.user.signature)

    def test_user_can_only_publish_own_images(self):
        foreign = self.env["ir.attachment"].create({"name": "geheim.png", "datas": PNG})
        with self.assertRaises(AccessError):
            self.user.with_user(self.user).write(
                {"mail_sync_signature_extra": f'<p><img src="/web/image/{foreign.id}"></p>'}
            )
        self.assertFalse(foreign.public)

    def test_user_own_image_published(self):
        own = self.env["ir.attachment"].with_user(self.user).create({"name": "banner.png", "datas": PNG})
        self.user.with_user(self.user).write(
            {"mail_sync_signature_extra": f'<p><img src="/web/image/{own.id}-abc/banner.png?access_token=x"></p>'}
        )
        self.assertTrue(own.sudo().public)
        self.assertIn(f"https://odoo.example/web/image/{own.id}/banner.png", self.user.mail_sync_signature_extra)

    def test_template_data_uri_becomes_public_attachment(self):
        self.template.body_html = f'<p><img src="data:image/png;base64,{PNG}"></p>'
        self.assertIn("https://odoo.example/web/image/", self.template.body_html)

    def test_preview(self):
        self.template.preview_user_id = self.user
        self.assertIn("Paula Person", self.template.preview_html)
