# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Pure pre-filter rules applied to a message before any matching. No ORM access.

All address inputs are plain e-mail strings; comparisons are case-insensitive.
"""

import re

AUTO_SUBMITTED_VALUES = ("auto-replied", "auto-generated", "auto-notified")
AUTOMATIC_HEADER_NAMES = ("x-auto-response-suppress", "x-autoreply", "x-autorespond")
BULK_PRECEDENCE = ("bulk", "junk", "list", "auto_reply")
CALENDAR_TYPES = ("text/calendar", "application/ics")
DEFAULT_FREEMAIL_DOMAINS = (
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "outlook.de",
    "hotmail.com",
    "hotmail.de",
    "live.com",
    "live.de",
    "yahoo.com",
    "yahoo.de",
    "web.de",
    "gmx.de",
    "gmx.net",
    "gmx.at",
    "gmx.ch",
    "t-online.de",
    "icloud.com",
    "me.com",
    "aol.com",
    "freenet.de",
    "posteo.de",
    "mail.de",
    "protonmail.com",
    "proton.me",
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)


def normalize(address):
    """Lower-cased bare address or '' for garbage."""
    if not address:
        return ""
    match = _EMAIL_RE.search(str(address))
    return match.group(0).lower() if match else ""


def split_domain(address):
    address = normalize(address)
    return address.rsplit("@", 1)[1] if "@" in address else ""


def _domains(domains):
    return {d.strip().lower().lstrip("@") for d in (domains or []) if d and d.strip()}


def is_internal(addresses, internal_domains):
    """True when every address belongs to one of the organisation's own domains."""
    domains = _domains(internal_domains)
    normalized = [normalize(a) for a in (addresses or [])]
    normalized = [a for a in normalized if a]
    if not normalized or not domains:
        return False
    return all(split_domain(a) in domains for a in normalized)


def external_addresses(addresses, internal_domains):
    """Unique external addresses in original order."""
    domains = _domains(internal_domains)
    seen, result = set(), []
    for address in addresses or []:
        a = normalize(address)
        if a and a not in seen and split_domain(a) not in domains:
            seen.add(a)
            result.append(a)
    return result


def is_blocked(addresses, blocklist_addresses, blocklist_domains):
    blocked = {normalize(a) for a in (blocklist_addresses or []) if normalize(a)}
    domains = _domains(blocklist_domains)
    for address in addresses or []:
        a = normalize(address)
        if a and (a in blocked or split_domain(a) in domains):
            return True
    return False


def is_freemail(address, freemail_domains=DEFAULT_FREEMAIL_DOMAINS):
    return split_domain(address) in _domains(freemail_domains)


def is_automatic(headers, attachments=None, content_type=None):
    """Auto-replies, bounces, calendar invitations and bulk mail.

    ``headers``: mapping of header name → value (case-insensitive lookup).
    ``attachments``: iterable of dicts with ``name`` and/or ``contentType``.
    """
    lowered = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items() if v is not None}
    auto_submitted = lowered.get("auto-submitted", "")
    if any(v in auto_submitted for v in AUTO_SUBMITTED_VALUES):
        return True
    if any(name in lowered for name in AUTOMATIC_HEADER_NAMES):
        return True
    if lowered.get("precedence", "") in BULK_PRECEDENCE:
        return True
    if content_type and ("multipart/report" in content_type.lower() or content_type.lower() in CALENDAR_TYPES):
        return True
    for attachment in attachments or []:
        name = str(attachment.get("name") or "").lower()
        ctype = str(attachment.get("contentType") or "").lower()
        if name.endswith(".ics") or ctype in CALENDAR_TYPES:
            return True
    return False


def is_private(subject, folder_name, subject_prefixes, private_folder_names=("privat", "private", "persönlich")):
    """Private by subject prefix (e.g. '[PRIVAT]') or by folder name."""
    subject_l = (subject or "").strip().lower()
    for prefix in subject_prefixes or []:
        if prefix and subject_l.startswith(prefix.strip().lower()):
            return True
    folder_l = (folder_name or "").strip().lower()
    return bool(folder_l) and folder_l in {n.lower() for n in private_folder_names}
