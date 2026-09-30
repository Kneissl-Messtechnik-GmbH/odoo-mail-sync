# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Import a raw e-mail into the chatter of a record without notifying anyone."""

import email
import email.policy
import logging

from odoo.tools import html_escape

from . import prefilter as pf

_logger = logging.getLogger(__name__)

INGEST_CONTEXT = {
    "mail_sync_import": True,
    "mail_create_nosubscribe": True,
    "mail_create_nolog": True,
    "mail_notrack": True,
    "mail_post_autofollow": False,
    "mail_notify_force_send": False,
    "tracking_disable": True,
}


def parse_mime(env, mime_bytes):
    """Odoo's own parser: returns dict(message_id, subject, body, attachments, date, author_id, email_from, references …)."""
    message = email.message_from_bytes(mime_bytes, policy=email.policy.SMTP)
    parsed = env["mail.thread"].message_parse(message, save_original=False)
    headers = {k: v for k, v in message.items()}
    parsed["_headers"] = headers
    parsed["_content_type"] = message.get_content_type()
    return parsed


def is_automatic(parsed):
    attachments = [
        {"name": a.fname, "contentType": ""} for a in parsed.get("attachments") or [] if getattr(a, "fname", None)
    ]
    return pf.is_automatic(parsed.get("_headers"), attachments=attachments, content_type=parsed.get("_content_type"))


def find_existing(env, message_id):
    if not message_id:
        return env["mail.message"].browse()
    return env["mail.message"].sudo().search([("message_id", "=", message_id)], limit=1)  # sudo: dedupe across records


def thread_target_from_references(env, parsed):
    """D1/K: a reply to a message already in Odoo continues on that record."""
    refs = [r.strip() for r in (parsed.get("references") or "").split() if r.strip()]
    if parsed.get("in_reply_to"):
        refs.append(parsed["in_reply_to"].strip())
    if not refs:
        return None
    existing = (
        env["mail.message"]
        .sudo()
        .search(  # sudo: threading must see all records
            [("message_id", "in", refs[-32:]), ("model", "!=", False), ("res_id", "!=", 0)], order="id desc", limit=1
        )
    )
    if existing and existing.model in env:
        record = env[existing.model].browse(existing.res_id).exists()
        return record or None
    return None


def post(env, target, parsed, max_attachment_bytes, web_link=None):
    """Post the parsed mail as a note-type e-mail on ``target``. Returns the mail.message."""
    attachments, dropped = [], []
    for att in parsed.get("attachments") or []:
        content = att.content if isinstance(att.content, bytes) else (att.content or "").encode()
        if len(content) > max_attachment_bytes:
            dropped.append(f"{att.fname} ({len(content) // 1024 // 1024} MB)")
            continue
        attachments.append((att.fname, content))
    body = parsed.get("body") or ""
    if dropped:
        note = ", ".join(html_escape(d) for d in dropped)
        link = f' <a href="{html_escape(web_link)}" target="_blank">In Outlook öffnen</a>' if web_link else ""
        body = f'<p class="text-muted"><small>Nicht übernommene Anhänge: {note}.{link}</small></p>' + body
    thread = target.with_context(**INGEST_CONTEXT)
    return thread.message_post(
        body=body,
        subject=parsed.get("subject"),
        message_type="email",
        subtype_xmlid="mail.mt_note",
        author_id=parsed.get("author_id") or False,
        email_from=parsed.get("email_from"),
        date=parsed.get("date"),
        message_id=parsed.get("message_id"),
        partner_ids=[],
        attachments=attachments,
        record_name=target.display_name,
    )
