# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Delegated mode: each user connects their own mailbox through the Microsoft authorization-code flow."""

import hashlib
import hmac
import json
import logging
from urllib.parse import urlencode

from odoo import http
from odoo.http import request
from werkzeug.exceptions import BadRequest, Forbidden

from ..services.graph import DELEGATED_SCOPES, LOGIN_BASE

_logger = logging.getLogger(__name__)


def _sign(payload, secret):
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _state_secret(env):
    return env["ir.config_parameter"].sudo().get_param("database.secret")  # sudo: key material only


def make_state(env, mailbox_id, user_id):
    payload = json.dumps({"m": mailbox_id, "u": user_id})
    return f"{payload}.{_sign(payload, _state_secret(env))}"


def read_state(env, state):
    try:
        payload, signature = state.rsplit(".", 1)
    except (ValueError, AttributeError) as exc:
        raise BadRequest("invalid state") from exc
    if not hmac.compare_digest(signature, _sign(payload, _state_secret(env))):
        raise BadRequest("invalid state signature")
    return json.loads(payload)


def redirect_uri(env):
    base = env["ir.config_parameter"].sudo().get_param("web.base.url")  # sudo: public parameter
    return f"{base}/mail_sync/oauth/callback"


def exchange_code(account, code, redirect):
    """Authorization-code exchange. Separated so tests can mock it."""
    import msal  # noqa: PLC0415

    app = msal.ConfidentialClientApplication(
        account.client_id, authority=f"{LOGIN_BASE}/{account.tenant_id}", client_credential=account._get_secret()
    )
    return app.acquire_token_by_authorization_code(code, scopes=DELEGATED_SCOPES, redirect_uri=redirect)


class MailSyncOAuthController(http.Controller):
    @http.route("/mail_sync/oauth/start", type="http", auth="user")
    def start(self, mailbox_id=None, **kw):
        mailbox = request.env["mail.sync.mailbox"].browse(int(mailbox_id or 0)).exists()
        if not mailbox:
            raise BadRequest("mailbox not found")
        user = request.env.user
        if mailbox.owner_user_id != user and not user.has_group("mail_sync_microsoft.group_mail_sync_manager"):
            raise Forbidden()
        account = mailbox.account_id
        params = {
            "client_id": account.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri(request.env),
            "response_mode": "query",
            "scope": " ".join(["offline_access", *DELEGATED_SCOPES]),
            "state": make_state(request.env, mailbox.id, user.id),
            "login_hint": mailbox.upn,
            "prompt": "select_account",
        }
        return request.redirect(
            f"{LOGIN_BASE}/{account.tenant_id}/oauth2/v2.0/authorize?{urlencode(params)}", local=False
        )

    @http.route("/mail_sync/oauth/callback", type="http", auth="user")
    def callback(self, code=None, state=None, error=None, error_description=None, **kw):
        data = read_state(request.env, state or "")
        mailbox = request.env["mail.sync.mailbox"].browse(data["m"]).exists()
        if not mailbox or data["u"] != request.env.user.id:
            raise Forbidden()
        target = f"/odoo/action-mail_sync_microsoft.mail_sync_mailbox_action/{mailbox.id}"
        if error or not code:
            mailbox.write({"state": "auth_error", "last_error": f"{error}: {error_description}"})
            return request.redirect(target)
        result = exchange_code(mailbox.account_id, code, redirect_uri(request.env))
        if "refresh_token" not in result:
            mailbox.write(
                {"state": "auth_error", "last_error": result.get("error_description") or "kein Refresh-Token"}
            )
            return request.redirect(target)
        mailbox._store_refresh_token(result["refresh_token"])
        mailbox.write({"state": "connected", "last_error": False})
        mailbox.action_discover_folders()
        return request.redirect(target)
