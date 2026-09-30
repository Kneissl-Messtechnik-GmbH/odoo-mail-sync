# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Folder discovery, backfill, delta and the per-message pipeline."""

import logging
from datetime import datetime, timedelta

from odoo import fields

from . import ingest as ingest_service
from . import prefilter as pf
from .router import get_router

_logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ helpers
def _addr(party):
    return pf.normalize(((party or {}).get("emailAddress") or {}).get("address"))


def _dt(value):
    if not value:
        return False
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    if parsed.tzinfo is not None:
        parsed = (parsed - parsed.utcoffset()).replace(tzinfo=None)
    return parsed


def normalize_row(row, folder=None, internal_domains=()):
    from_email = _addr(row.get("from")) or _addr(row.get("sender"))
    to_emails = [a for a in (_addr(p) for p in row.get("toRecipients") or []) if a]
    cc_emails = [a for a in (_addr(p) for p in row.get("ccRecipients") or []) if a]
    outbound = bool(from_email) and pf.split_domain(from_email) in {d.lower() for d in internal_domains}
    if folder is not None and (folder.well_known_name or "") == "sentitems":
        outbound = True
    return {
        "graph_id": row.get("id"),
        "internet_message_id": (row.get("internetMessageId") or "").strip() or False,
        "conversation_id": row.get("conversationId") or False,
        "subject": (row.get("subject") or "")[:500],
        "from_email": from_email,
        "to_emails": to_emails,
        "cc_emails": cc_emails,
        "received": _dt(row.get("receivedDateTime") or row.get("sentDateTime")),
        "direction": "outbound" if outbound else "inbound",
        "has_attachments": bool(row.get("hasAttachments")),
        "body_preview": (row.get("bodyPreview") or "")[:255],
        "web_link": row.get("webLink") or False,
        "is_draft": bool(row.get("isDraft")),
        "folder_name": folder.display_name if folder is not None else "",
    }


def _since_iso(mailbox):
    start = mailbox.start_date or fields.Date.today()
    return f"{start.isoformat()}T00:00:00Z"


# --------------------------------------------------------------- discovery
def discover_folders(mailbox, run):
    graph = mailbox._graph()
    Folder = mailbox.env["mail.sync.folder"]
    existing = {f.graph_id: f for f in mailbox.folder_ids}
    seen = set()
    for row in graph.list_folders(mailbox.upn):
        seen.add(row["id"])
        vals = {
            "display_name": row.get("displayName") or "?",
            "path": row.get("path") or row.get("displayName"),
            "well_known_name": (row.get("wellKnownName") or "").lower(),
        }
        folder = existing.get(row["id"])
        if folder:
            folder.write(vals)
        else:
            vals.update(
                mailbox_id=mailbox.id,
                graph_id=row["id"],
                include=Folder._default_include(vals["well_known_name"], vals["display_name"], mailbox),
            )
            Folder.create(vals)
            run.bump("created")
        run.bump("fetched")
    # folders deleted in Outlook: keep rows but exclude them
    for graph_id, folder in existing.items():
        if graph_id not in seen and folder.include:
            folder.write({"include": False})
    return f"{run.fetched} Ordner"


# -------------------------------------------------------------- backfill
def backfill(mailbox, folder, run):
    """Initial load from ``start_date`` through a filtered delta, which also yields the delta link."""
    graph = mailbox._graph()
    rows, link = graph.delta(mailbox.upn, folder.graph_id, delta_link=None, since=_since_iso(mailbox))
    process_rows(mailbox, folder, rows, run)
    folder.write(
        {"delta_link": link, "backfill_done": True, "needs_full_resync": False, "last_sync": fields.Datetime.now()}
    )
    return f"{run.fetched} Nachrichten"


def delta(mailbox, folder, run):
    graph = mailbox._graph()
    if not folder.delta_link or folder.needs_full_resync:
        return backfill(mailbox, folder, run)
    rows, link = graph.delta(mailbox.upn, folder.graph_id, delta_link=folder.delta_link)
    process_rows(mailbox, folder, rows, run)
    folder.write({"delta_link": link or folder.delta_link, "last_sync": fields.Datetime.now()})
    return f"{run.fetched} Änderungen"


# -------------------------------------------------------------- pipeline
def process_rows(mailbox, folder, rows, run):
    Message = mailbox.env["mail.sync.message"]
    for row in rows:
        run.bump("fetched")
        try:
            with mailbox.env.cr.savepoint():
                if row.get("@removed"):
                    existing = Message.search(
                        [("mailbox_id", "=", mailbox.id), ("graph_id", "=", row.get("id"))], limit=1
                    )
                    if existing and existing.state != "removed":
                        existing.write({"state": "removed", "reason": "In Microsoft 365 gelöscht oder verschoben"})
                    continue
                process_message(mailbox, folder, row, run)
                mailbox.env.flush_all()
        except Exception as exc:  # noqa: BLE001 - one message must not stop the folder
            if type(exc).__name__ == "RetryableJobError" or exc.__class__.__module__.endswith("services.graph"):
                raise
            run.bump("errors")
            _logger.exception("Mail Sync: Fehler bei Nachricht %s", row.get("id"))
            existing = Message.search([("mailbox_id", "=", mailbox.id), ("graph_id", "=", row.get("id"))], limit=1)
            if existing:
                existing.write({"state": "error", "error": str(exc)[:2000]})


def process_message(mailbox, folder, row, run):
    env = mailbox.env
    Message = env["mail.sync.message"]
    msg = normalize_row(row, folder, mailbox.account_id._internal_domain_list())
    if msg["is_draft"] or not msg["graph_id"]:
        run.bump("skipped")
        return None
    registry = Message.search([("mailbox_id", "=", mailbox.id), ("graph_id", "=", msg["graph_id"])], limit=1)
    header_vals = {
        "folder_id": folder.id,
        "internet_message_id": msg["internet_message_id"],
        "conversation_id": msg["conversation_id"],
        "subject": msg["subject"],
        "email_from": msg["from_email"],
        "to_emails": ", ".join(msg["to_emails"])[:1000],
        "cc_emails": ", ".join(msg["cc_emails"])[:1000],
        "received_at": msg["received"],
        "direction": msg["direction"],
        "has_attachments": msg["has_attachments"],
        "body_preview": msg["body_preview"],
        "web_link": msg["web_link"],
    }
    if registry and registry.state in ("linked", "private") and registry.mail_message_id:
        registry.write(header_vals)  # header refresh only; already imported
        run.bump("skipped")
        return registry
    if registry and registry.state == "linked" and registry.model:
        run.bump("skipped")
        return registry

    # duplicate of a mail already in Odoo (e.g. sent by Odoo itself, or synced from another mailbox)
    existing_message = ingest_service.find_existing(env, msg["internet_message_id"])

    result = get_router(mailbox).route(msg)
    vals = dict(
        header_vals,
        state=result.state,
        reason=result.reason[:500] if result.reason else False,
        visibility=result.visibility,
        partner_ids=[(6, 0, result.partners.ids if result.partners else [])],
        commercial_partner_ids=[(6, 0, result.companies.ids if result.companies else [])],
        model=result.target._name if result.target else False,
        res_id=result.target.id if result.target else 0,
        error=False,
    )
    if existing_message and existing_message.model:
        vals.update(
            state="linked",
            model=existing_message.model,
            res_id=existing_message.res_id,
            mail_message_id=existing_message.id,
            reason="Bereits in Odoo vorhanden (Message-ID)",
        )
    if registry:
        registry.write(vals)
    else:
        registry = Message.create(dict(vals, mailbox_id=mailbox.id, graph_id=msg["graph_id"]))
        run.bump("created")

    if registry.state == "linked" and registry.visibility == "shared" and not registry.mail_message_id:
        ingest_registry_row(registry, run)
    elif registry.state == "linked":
        run.bump("linked")
    return registry


def ingest_registry_row(registry, run=None):
    """Fetch the MIME and post it on the registry's target; sets state/reason on failure."""
    mailbox = registry.mailbox_id
    env = registry.env
    target = registry._target_record()
    if target is None:
        registry.write({"state": "matched" if registry.partner_ids else "unmatched", "reason": "Zieldatensatz fehlt"})
        return False
    if registry.mail_message_id:
        return registry.mail_message_id
    existing = ingest_service.find_existing(env, registry.internet_message_id)
    if existing:
        registry.write(
            {"mail_message_id": existing.id, "model": existing.model, "res_id": existing.res_id, "state": "linked"}
        )
        return existing
    mime = mailbox._graph().mime(mailbox.upn, registry.graph_id)
    parsed = ingest_service.parse_mime(env, mime)
    if ingest_service.is_automatic(parsed):
        registry.write(
            {"state": "skipped_automatic", "reason": "Automatische Nachricht (Auto-Reply, Bounce, Kalender)"}
        )
        if run:
            run.bump("skipped")
        return False
    threaded = ingest_service.thread_target_from_references(env, parsed)
    if threaded is not None and threaded != target:
        target = threaded
        registry.write(
            {
                "model": target._name,
                "res_id": target.id,
                "reason": f"D1: Antwort in bestehender Konversation auf {target.display_name}"[:500],
            }
        )
    max_bytes = max(mailbox.account_id.attachment_max_mb, 1) * 1024 * 1024
    message = ingest_service.post(env, target, parsed, max_bytes, web_link=registry.web_link)
    registry.write({"mail_message_id": message.id, "state": "linked", "error": False})
    if run:
        run.bump("linked")
    return message


def purge_before(env, mailbox, before: datetime):
    """Utility for tests and housekeeping: drop registry rows older than ``before`` without chatter entry."""
    env["mail.sync.message"].search(
        [("mailbox_id", "=", mailbox.id), ("received_at", "<", before), ("mail_message_id", "=", False)]
    ).unlink()


__all__ = [
    "backfill",
    "delta",
    "discover_folders",
    "ingest_registry_row",
    "normalize_row",
    "process_message",
    "process_rows",
    "purge_before",
    "timedelta",
]
