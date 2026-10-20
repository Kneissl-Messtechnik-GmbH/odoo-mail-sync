# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Signature rendering without Odoo: placeholders, empty-line cleanup, image URL rewriting.

Templates use ``{{placeholder}}`` in text and attributes. Values are HTML-escaped, except the
placeholders listed in ``RAW_PLACEHOLDERS`` which carry already sanitised HTML. Elements whose
placeholders all render empty disappear together with their label (``<p>Mobil: {{mobile}}</p>``),
so one template serves people with and without mobile number, photo or job title.
"""

import re

from lxml import etree, html
from markupsafe import Markup, escape

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
# lxml (and Odoo's sanitizer, which uses it) percent-encodes "{" and "}" inside href/src on
# newer versions: "{{photo_url}}" comes back as "%7B%7Bphoto_url%7D%7D". Placeholders are
# therefore swapped for an inert token before any lxml round trip, and both spellings are
# accepted on input.
_ANY_PLACEHOLDER_RE = re.compile(r"(?:\{\{|%7[Bb]%7[Bb])\s*([a-z_][a-z0-9_]*)\s*(?:\}\}|%7[Dd]%7[Dd])")
_TOKEN_RE = re.compile(r"zzph([a-z_][a-z0-9_]*?)phzz")


def _tokenize(text):
    return _ANY_PLACEHOLDER_RE.sub(lambda m: f"zzph{m.group(1)}phzz", text)


def _detokenize(text):
    return _TOKEN_RE.sub(lambda m: "{{" + m.group(1) + "}}", text)


RAW_PLACEHOLDERS = {"extra"}

# Documented placeholders (shown in the template form).
PLACEHOLDERS = [
    ("name", "Vollständiger Name der sendenden Person"),
    ("first_name", "Vorname"),
    ("last_name", "Nachname"),
    ("function", "Funktion / Position (Kontakt)"),
    ("department", "Abteilung (falls gepflegt)"),
    ("email", "E-Mail-Adresse der Person"),
    ("phone", "Telefon (Kontakt der Person)"),
    ("phone_link", "Telefon für tel:-Links (nur Ziffern und +)"),
    ("mobile", "Mobilnummer"),
    ("mobile_link", "Mobilnummer für tel:-Links"),
    ("photo_url", "Öffentliche Adresse des Profilbilds"),
    ("extra", "Persönlicher Zusatz (HTML, vom Benutzer gepflegt)"),
    ("mailbox_name", "Name des Funktionspostfachs, über das gesendet wird"),
    ("mailbox_email", "Adresse des Funktionspostfachs"),
    ("company_name", "Firmenname"),
    ("company_street", "Straße"),
    ("company_zip", "PLZ"),
    ("company_city", "Ort"),
    ("company_phone", "Telefon der Firma"),
    ("company_phone_link", "Telefon der Firma für tel:-Links"),
    ("company_email", "E-Mail der Firma"),
    ("company_website", "Webseite (ohne https://)"),
    ("company_website_url", "Webseite als vollständige Adresse"),
    ("company_logo_url", "Öffentliche Adresse des Firmenlogos"),
    ("company_registry", "Handelsregister"),
    ("company_vat", "USt-IdNr."),
]

# Elements that only make sense with content; emptied cells are kept so tables keep their shape.
_KEEP_TAGS = {"td", "th", "body", "html"}
_IMG_SRC_RE = re.compile(r"^(?:https?://[^/]+)?/web/(?:image|content)/(\d+)")


def tel_link(number):
    """``+49 (0)7152 1234-5`` -> ``+49715212345`` (drops the national trunk prefix in brackets)."""
    if not number:
        return ""
    number = re.sub(r"\(0\)", "", number)
    cleaned = re.sub(r"[^\d+]", "", number)
    return cleaned


def placeholders_in(text):
    text = text or ""
    return _TOKEN_RE.findall(text) + _ANY_PLACEHOLDER_RE.findall(text)


def _own_placeholders(element):
    """Placeholders in the element's own text, its attributes and the tails of its children."""
    parts = [element.text or ""]
    parts.extend(child.tail or "" for child in element)
    parts.extend(str(v) for v in element.attrib.values())
    return placeholders_in("".join(parts))


def _is_label_only(element):
    """True when nothing but whitespace or a short label ending in ':' is left."""
    if len(element):
        return False
    text = (element.text or "").strip()
    return not text or (text.endswith(":") and len(text) <= 40)


def _drop(element):
    parent = element.getparent()
    if parent is None:
        return
    if element.tag in _KEEP_TAGS:
        for child in list(element):
            element.remove(child)
        element.text = ""
        return
    # keep the text following the element
    tail = element.tail
    previous = element.getprevious()
    if tail:
        if previous is not None:
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(element)
    if parent.tag not in _KEEP_TAGS and parent.getparent() is not None and _is_label_only(parent):
        _drop(parent)


def render(template_html, values):
    """Render ``template_html`` with ``values`` and return Markup.

    ``values`` maps placeholder names to plain strings (escaped here) or Markup for
    ``RAW_PLACEHOLDERS``. Unknown placeholders render empty.
    """
    if not template_html or not str(template_html).strip():
        return Markup("")

    def value(name):
        return values.get(name) or ""

    root = html.fragment_fromstring(_tokenize(str(template_html)), create_parent="div")
    # innermost first, so that a removed child can make its parent label-only
    for element in reversed(list(root.iter())):
        if element is root or not isinstance(element.tag, str):
            continue
        names = _own_placeholders(element)
        if element.tag == "img" and placeholders_in(element.get("src")):
            # an image without its source is broken, whatever its alt text says
            names = placeholders_in(element.get("src"))
        if names and not any(str(value(n)).strip() for n in names):
            _drop(element)
    rendered = "".join([root.text or ""] + [etree.tostring(child, encoding="unicode", method="html") for child in root])

    def substitute(match):
        name = match.group(1)
        raw = value(name)
        if name in RAW_PLACEHOLDERS:
            return str(raw)
        return str(escape(raw))

    # template HTML is sanitised by the field, every substituted value is escaped (or pre-sanitised extra)
    return Markup(_TOKEN_RE.sub(substitute, rendered))  # noqa: S704


def rewrite_image_sources(body_html, resolve):
    """Replace every ``<img src>`` through ``resolve(src) -> new_src or None``.

    Used to turn editor uploads (``/web/image/123-abc/x.png?access_token=…``) and inline
    ``data:`` images into stable, public, absolute URLs that recipients can load.
    Returns the new HTML (str) or the input unchanged when nothing changed.
    """
    if not body_html or "<img" not in str(body_html):
        return body_html
    root = html.fragment_fromstring(_tokenize(str(body_html)), create_parent="div")
    changed = False
    for img in root.iter("img"):
        src = img.get("src") or ""
        if not src or _TOKEN_RE.search(src):
            continue
        new_src = resolve(src)
        if new_src and new_src != src:
            img.set("src", new_src)
            changed = True
    if not changed:
        return body_html
    return _detokenize(
        "".join([root.text or ""] + [etree.tostring(child, encoding="unicode", method="html") for child in root])
    )


def attachment_id_from_src(src):
    match = _IMG_SRC_RE.match(src or "")
    return int(match.group(1)) if match else None
