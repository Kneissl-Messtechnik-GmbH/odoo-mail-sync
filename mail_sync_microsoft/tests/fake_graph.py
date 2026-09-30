# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""In-memory stand-in for GraphClient used by the tests. Same public methods, no network."""

import email.utils
from datetime import datetime
from email.message import EmailMessage

from ..services.graph import GraphGone, GraphThrottled

WELL_KNOWN = {"inbox": "Posteingang", "sentitems": "Gesendete Elemente", "archive": "Archiv", "drafts": "Entwürfe"}


def party(address, name=None):
    return {"emailAddress": {"address": address, "name": name or address}}


class FakeGraph:
    def __init__(self, domains=("kneissl-messtechnik.de",)):
        self.domains = list(domains)
        self.mailboxes = {}  # upn -> {"folders": {folder_id: folder}, "messages": {folder_id: [msg]}}
        self.mime_store = {}  # message id -> bytes
        self.removed = {}  # folder_id -> [message ids removed in next delta]
        self.calls = []
        self.throttle_once = False
        self.gone_once = False
        self.delta_counter = 0

    # --- setup helpers -------------------------------------------------------
    def add_mailbox(self, upn):
        self.mailboxes[upn] = {"folders": {}, "messages": {}}
        for well_known, name in WELL_KNOWN.items():
            self.add_folder(upn, f"{upn}:{well_known}", name, well_known)
        return upn

    def add_folder(self, upn, folder_id, name, well_known="", parent=None):
        self.mailboxes[upn]["folders"][folder_id] = {
            "id": folder_id,
            "displayName": name,
            "wellKnownName": well_known,
            "parentFolderId": parent,
            "childFolderCount": 0,
            "totalItemCount": 0,
        }
        self.mailboxes[upn]["messages"].setdefault(folder_id, [])
        return folder_id

    def add_message(
        self,
        upn,
        folder,
        message_id,
        subject,
        sender,
        to,
        cc=None,
        received="2026-09-01T10:00:00Z",
        body="Hallo",
        conversation_id=None,
        headers=None,
        attachments=None,
        internet_message_id=None,
        in_reply_to=None,
    ):
        folder_id = f"{upn}:{folder}" if ":" not in folder else folder
        internet_message_id = internet_message_id or f"<{message_id}@fake.test>"
        row = {
            "id": message_id,
            "internetMessageId": internet_message_id,
            "conversationId": conversation_id or f"conv-{message_id}",
            "subject": subject,
            "from": party(sender),
            "sender": party(sender),
            "toRecipients": [party(a) for a in to],
            "ccRecipients": [party(a) for a in (cc or [])],
            "replyTo": [],
            "receivedDateTime": received,
            "sentDateTime": received,
            "isDraft": False,
            "hasAttachments": bool(attachments),
            "bodyPreview": body[:255],
            "parentFolderId": folder_id,
            "webLink": f"https://outlook.office.com/mail/id/{message_id}",
        }
        self.mailboxes[upn]["messages"][folder_id].append(row)
        msg = EmailMessage()
        msg["From"] = sender
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Message-ID"] = internet_message_id
        msg["Date"] = email.utils.format_datetime(datetime.fromisoformat(received.replace("Z", "+00:00")))
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        for key, value in (headers or {}).items():
            msg[key] = value
        msg.set_content(body)
        for att in attachments or []:
            if att.get("rfc822"):
                inner = EmailMessage()
                inner["Subject"] = att.get("subject", "Weitergeleitet")
                inner["From"] = "x@example.com"
                inner.set_content("Original")
                if not msg.is_multipart():
                    msg.make_mixed()
                msg.attach(inner)
                continue
            msg.add_attachment(
                att.get("data", b"x"),
                maintype=att.get("maintype", "application"),
                subtype=att.get("subtype", "octet-stream"),
                filename=att["name"],
            )
        self.mime_store[message_id] = msg.as_bytes()
        return row

    def remove_message(self, upn, folder, message_id):
        folder_id = f"{upn}:{folder}" if ":" not in folder else folder
        msgs = self.mailboxes[upn]["messages"][folder_id]
        self.mailboxes[upn]["messages"][folder_id] = [m for m in msgs if m["id"] != message_id]
        self.removed.setdefault(folder_id, []).append(message_id)

    # --- GraphClient interface -------------------------------------------------
    def organization(self):
        return {"id": "org", "displayName": "Fake", "verifiedDomains": [{"name": d} for d in self.domains]}

    def verified_domains(self):
        return list(self.domains)

    def list_folders(self, upn):
        self.calls.append(("list_folders", upn))
        folders = []
        for f in self.mailboxes[upn]["folders"].values():
            row = dict(f)
            parent = self.mailboxes[upn]["folders"].get(f.get("parentFolderId"))
            row["path"] = f"{parent['displayName']}/{f['displayName']}" if parent else f["displayName"]
            folders.append(row)
        return folders

    def _maybe_fail(self):
        if self.throttle_once:
            self.throttle_once = False
            raise GraphThrottled(7)
        if self.gone_once:
            self.gone_once = False
            raise GraphGone("syncStateNotFound", status=410)

    def delta(self, upn, folder_id, delta_link=None, since=None):
        self.calls.append(("delta", upn, folder_id, bool(delta_link)))
        self._maybe_fail()
        self.delta_counter += 1
        rows = []
        seen = set()
        if delta_link:
            seen = set(delta_link.split("|")[2:]) if delta_link.count("|") >= 2 else set()
        for m in self.mailboxes[upn]["messages"].get(folder_id, []):
            if m["id"] in seen:
                continue
            if since and m["receivedDateTime"] < since:
                continue
            rows.append(dict(m))
        for removed_id in self.removed.pop(folder_id, []):
            rows.append({"id": removed_id, "@removed": {"reason": "deleted"}})
        known = seen | {m["id"] for m in self.mailboxes[upn]["messages"].get(folder_id, [])}
        new_link = "|".join(["delta", str(self.delta_counter), *sorted(known)])
        return rows, new_link

    page_size = 2

    def delta_page(self, upn, folder_id, link=None, since=None):
        """Paged variant of ``delta``: a round is computed once and served in ``page_size`` slices."""
        cache = self.__dict__.setdefault("_rounds", {})
        if link and link.startswith("next|"):
            _, offset, key = link.split("|", 2)
            offset = int(offset)
            rows, delta_link = cache[key]
        else:
            offset = 0
            rows, delta_link = self.delta(upn, folder_id, delta_link=link or None, since=since)
            key = f"{folder_id}:{self.delta_counter}"
            cache[key] = (rows, delta_link)
        page = rows[offset : offset + self.page_size]
        if offset + self.page_size < len(rows):
            return page, f"next|{offset + self.page_size}|{key}", None
        cache.pop(key, None)
        return page, None, delta_link

    def messages_since(self, upn, folder_id, since, until=None):
        self.calls.append(("messages_since", upn, folder_id))
        for m in self.mailboxes[upn]["messages"].get(folder_id, []):
            if m["receivedDateTime"] >= since and (not until or m["receivedDateTime"] < until):
                yield dict(m)

    def message(self, upn, message_id, select=None):
        for msgs in self.mailboxes[upn]["messages"].values():
            for m in msgs:
                if m["id"] == message_id:
                    return dict(m)
        return None

    def mime(self, upn, message_id, max_bytes=None):
        self.calls.append(("mime", upn, message_id))
        return self.mime_store[message_id]

    def attachments_meta(self, upn, message_id):
        return []
