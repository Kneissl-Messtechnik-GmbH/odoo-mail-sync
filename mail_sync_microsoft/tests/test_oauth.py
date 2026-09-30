# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from unittest import mock
from urllib.parse import quote

from odoo.tests.common import HttpCase, tagged
from werkzeug.exceptions import BadRequest

from ..controllers import oauth
from .common import MailSyncCase


class TestOAuthState(MailSyncCase):
    def test_state_roundtrip_and_tamper(self):
        state = oauth.make_state(self.env, 42, 7)
        self.assertEqual(oauth.read_state(self.env, state), {"m": 42, "u": 7})
        with self.assertRaises(BadRequest):
            oauth.read_state(self.env, state[:-2] + "zz")
        with self.assertRaises(BadRequest):
            oauth.read_state(self.env, "garbage")

    def test_refresh_token_encryption(self):
        self.mailbox._store_refresh_token("secret-token")
        self.assertNotEqual(self.mailbox.sudo().refresh_token_enc, "secret-token")
        self.assertEqual(self.mailbox._load_refresh_token(), "secret-token")


@tagged("post_install", "-at_install")
class TestOAuthCallback(HttpCase):
    def test_callback_stores_token(self):
        account = self.env["mail.sync.account"].create(
            {"name": "Deleg", "tenant_id": "t", "client_id": "c", "auth_mode": "delegated"}
        )
        user = self.env.ref("base.user_admin")
        mailbox = self.env["mail.sync.mailbox"].create(
            {"account_id": account.id, "upn": "admin@example.com", "owner_user_id": user.id}
        )
        state = oauth.make_state(self.env, mailbox.id, user.id)
        self.authenticate("admin", "admin")
        with mock.patch.object(oauth, "exchange_code", return_value={"refresh_token": "rt", "access_token": "at"}):
            response = self.url_open(f"/mail_sync/oauth/callback?code=abc&state={quote(state)}", allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        mailbox.invalidate_recordset()
        self.assertEqual(mailbox.state, "connected")
        self.assertEqual(mailbox._load_refresh_token(), "rt")
