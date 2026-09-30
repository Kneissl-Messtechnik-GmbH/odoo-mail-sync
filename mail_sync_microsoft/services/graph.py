# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Microsoft Graph client for mail: token providers, folders, delta queries, MIME download.

The client knows nothing about Odoo. Errors are mapped to a small exception family so that
callers can decide between retry (throttled), full resync (gone) and re-authentication.
"""

import logging
import time

import requests

_logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
LOGIN_BASE = "https://login.microsoftonline.com"
APP_SCOPES = ["https://graph.microsoft.com/.default"]
DELEGATED_SCOPES = ["https://graph.microsoft.com/Mail.Read"]

# Fields fetched from delta / list calls. Headers and body come with the MIME download.
MESSAGE_SELECT = ",".join(
    [
        "id",
        "internetMessageId",
        "conversationId",
        "subject",
        "from",
        "sender",
        "toRecipients",
        "ccRecipients",
        "replyTo",
        "receivedDateTime",
        "sentDateTime",
        "isDraft",
        "hasAttachments",
        "bodyPreview",
        "parentFolderId",
        "webLink",
    ]
)
FOLDER_SELECT = "id,displayName,wellKnownName,parentFolderId,childFolderCount,totalItemCount"

# Well-known folders that are never synchronised.
EXCLUDED_WELL_KNOWN = {
    "deleteditems",
    "drafts",
    "junkemail",
    "outbox",
    "clutter",
    "conversationhistory",
    "recoverableitemsdeletions",
    "syncissues",
    "conflicts",
    "localfailures",
    "serverfailures",
    "scheduled",
    "searchfolders",
}


class GraphError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class GraphThrottled(GraphError):
    def __init__(self, retry_after, message="throttled"):
        super().__init__(message, status=429)
        self.retry_after = retry_after


class GraphGone(GraphError):
    """Delta token no longer valid (HTTP 410 / syncStateNotFound): resync the folder."""


class GraphUnauthorized(GraphError):
    """Token rejected (HTTP 401/403): re-authenticate or fix permissions."""


class GraphNotFound(GraphError):
    """Mailbox, folder or message does not exist (HTTP 404)."""


# ----------------------------------------------------------------------------- tokens
class TokenProvider:
    """Returns a bearer token; implementations cache internally."""

    def get_token(self):
        raise NotImplementedError

    def invalidate(self):
        """Drop cached tokens (called after a 401)."""


class AppTokenProvider(TokenProvider):
    """Client-credentials flow (application permissions, scoped via Exchange RBAC)."""

    def __init__(self, tenant_id, client_id, client_secret=None, certificate=None):
        import msal  # noqa: PLC0415 - optional dependency, imported lazily

        credential = certificate or client_secret
        self._app = msal.ConfidentialClientApplication(
            client_id, authority=f"{LOGIN_BASE}/{tenant_id}", client_credential=credential
        )

    def get_token(self):
        result = self._app.acquire_token_silent(APP_SCOPES, account=None) or self._app.acquire_token_for_client(
            scopes=APP_SCOPES
        )
        if "access_token" not in result:
            raise GraphUnauthorized(result.get("error_description") or result.get("error") or "no token")
        return result["access_token"]

    def invalidate(self):
        self._app.remove_tokens_for_client()


class DelegatedTokenProvider(TokenProvider):
    """Refresh-token flow for a mailbox connected by its own user."""

    def __init__(self, tenant_id, client_id, client_secret, refresh_token, on_refresh=None):
        import msal  # noqa: PLC0415

        self._app = msal.ConfidentialClientApplication(
            client_id, authority=f"{LOGIN_BASE}/{tenant_id}", client_credential=client_secret
        )
        self._refresh_token = refresh_token
        self._on_refresh = on_refresh
        self._access_token = None
        self._expires_at = 0.0

    def get_token(self):
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        result = self._app.acquire_token_by_refresh_token(self._refresh_token, scopes=DELEGATED_SCOPES)
        if "access_token" not in result:
            raise GraphUnauthorized(result.get("error_description") or result.get("error") or "no token")
        self._access_token = result["access_token"]
        self._expires_at = time.time() + int(result.get("expires_in", 3600))
        new_refresh = result.get("refresh_token")
        if new_refresh and new_refresh != self._refresh_token:
            self._refresh_token = new_refresh
            if self._on_refresh:
                self._on_refresh(new_refresh)
        return self._access_token

    def invalidate(self):
        self._access_token = None


class StaticTokenProvider(TokenProvider):
    def __init__(self, token):
        self._token = token

    def get_token(self):
        return self._token


# ----------------------------------------------------------------------------- client
class GraphClient:
    def __init__(self, token_provider, session=None, base_url=GRAPH_BASE, timeout=60, max_page=100):
        self.tokens = token_provider
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_page = max_page

    # --- http --------------------------------------------------------------
    def _headers(self, extra=None):
        headers = {
            "Authorization": f"Bearer {self.tokens.get_token()}",
            "Prefer": f'IdType="ImmutableId", odata.maxpagesize={self.max_page}',
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method, url, params=None, extra_headers=None, stream=False, retried_auth=False):
        if not url.startswith("http"):
            url = f"{self.base_url}/{url.lstrip('/')}"
        try:
            response = self.session.request(
                method, url, params=params, headers=self._headers(extra_headers), timeout=self.timeout, stream=stream
            )
        except requests.RequestException as exc:
            raise GraphThrottled(60, f"network error: {exc}") from exc
        status = response.status_code
        if status in (429, 503, 504):
            retry_after = response.headers.get("Retry-After")
            seconds = int(retry_after) if retry_after and str(retry_after).isdigit() else 60
            raise GraphThrottled(seconds)
        if status == 401 and not retried_auth:
            self.tokens.invalidate()
            return self._request(method, url, params, extra_headers, stream, retried_auth=True)
        if status in (401, 403):
            raise GraphUnauthorized(_error_message(response), status=status)
        if status == 404:
            raise GraphNotFound(_error_message(response), status=404)
        if status == 410 or (status == 400 and "syncStateNotFound" in response.text):
            raise GraphGone(_error_message(response), status=status)
        if status >= 400:
            raise GraphError(_error_message(response), status=status)
        return response

    def get_json(self, url, params=None, extra_headers=None):
        return self._request("GET", url, params=params, extra_headers=extra_headers).json()

    def _paged(self, url, params=None):
        """Yield rows across @odata.nextLink pages; return the final @odata.deltaLink if any."""
        payload = self.get_json(url, params=params)
        while True:
            yield from payload.get("value", [])
            next_link = payload.get("@odata.nextLink")
            if not next_link:
                return payload.get("@odata.deltaLink")
            payload = self.get_json(next_link)

    # --- organisation ------------------------------------------------------
    def organization(self):
        payload = self.get_json("organization", params={"$select": "id,displayName,verifiedDomains"})
        return (payload.get("value") or [{}])[0]

    def verified_domains(self):
        org = self.organization()
        return [d.get("name", "").lower() for d in org.get("verifiedDomains", []) if d.get("name")]

    # --- folders -----------------------------------------------------------
    def list_folders(self, upn):
        """All mail folders of a mailbox, flattened, with ``parentFolderId`` and ``path``."""
        result = []

        def walk(url, prefix):
            rows = list(self._paged(url, params={"$select": FOLDER_SELECT, "$top": 100}) or [])
            for folder in rows:
                folder = dict(folder)
                folder["path"] = f"{prefix}/{folder.get('displayName', '')}".strip("/")
                folder["wellKnownName"] = (folder.get("wellKnownName") or "").lower()
                result.append(folder)
                if folder.get("childFolderCount"):
                    walk(f"users/{upn}/mailFolders/{folder['id']}/childFolders", folder["path"])

        walk(f"users/{upn}/mailFolders", "")
        return result

    # --- messages ----------------------------------------------------------
    def delta(self, upn, folder_id, delta_link=None, since=None):
        """One delta round. Returns (rows, delta_link).

        ``since`` (ISO datetime) limits the initial round; later rounds use ``delta_link``.
        Removed messages appear as {"id": ..., "@removed": {...}}.
        """
        if delta_link:
            url, params = delta_link, None
        else:
            url = f"users/{upn}/mailFolders/{folder_id}/messages/delta"
            params = {"$select": MESSAGE_SELECT}
            if since:
                params["$filter"] = f"receivedDateTime ge {since}"
        rows = []
        gen = self._paged(url, params=params)
        try:
            while True:
                rows.append(next(gen))
        except StopIteration as stop:
            return rows, stop.value

    def messages_since(self, upn, folder_id, since, until=None):
        """Backfill listing (no delta) for a time window; yields message dicts."""
        flt = f"receivedDateTime ge {since}"
        if until:
            flt += f" and receivedDateTime lt {until}"
        url = f"users/{upn}/mailFolders/{folder_id}/messages"
        params = {"$select": MESSAGE_SELECT, "$filter": flt, "$orderby": "receivedDateTime desc", "$top": 100}
        yield from (self._paged(url, params=params) or [])

    def message(self, upn, message_id, select=MESSAGE_SELECT):
        return self.get_json(f"users/{upn}/messages/{message_id}", params={"$select": select})

    def mime(self, upn, message_id, max_bytes=50 * 1024 * 1024):
        response = self._request("GET", f"users/{upn}/messages/{message_id}/$value", stream=True)
        chunks, size = [], 0
        for chunk in response.iter_content(chunk_size=256 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise GraphError(f"message {message_id} larger than {max_bytes} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def attachments_meta(self, upn, message_id):
        url = f"users/{upn}/messages/{message_id}/attachments"
        return list(self._paged(url, params={"$select": "id,name,contentType,size,isInline"}) or [])


def _error_message(response):
    try:
        payload = response.json()
        error = payload.get("error") or {}
        return f"{error.get('code', response.status_code)}: {error.get('message', '')}"[:500]
    except ValueError:
        return f"{response.status_code}: {response.text[:300]}"
