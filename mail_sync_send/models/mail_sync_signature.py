# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64
import binascii
import hashlib
import re

from markupsafe import Markup
from odoo import api, fields, models
from odoo.exceptions import AccessError
from odoo.tools import html_sanitize

from ..services import signature as signature_service

PUBLIC_RES_MODEL = "mail.sync.signature"
_DATA_URI_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", re.I | re.S)


class MailSyncSignature(models.Model):
    _name = "mail.sync.signature"
    _description = "Mail Sync: Signaturvorlage"
    _inherit = ["mail.thread"]
    _order = "sequence, name"

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    usage = fields.Selection(
        [("personal", "Persönlich (eigenes Postfach)"), ("shared", "Funktionspostfach (mit Ansprechperson)")],
        required=True,
        default="personal",
        tracking=True,
    )
    # Managers only (see access rules); attributes stay unsanitised so inline styles, tables and
    # tel:/mailto: links survive, tags are still sanitised (no scripts, no forms).
    body_html = fields.Html(
        string="Signatur",
        sanitize_attributes=False,
        sanitize_form=True,
        help="Platzhalter wie {{name}} oder {{photo_url}} werden je Person ersetzt. Absätze, deren Platzhalter "
        "leer sind, entfallen samt Beschriftung (z. B. „Mobil: {{mobile}}“ ohne Mobilnummer).",
    )
    preview_user_id = fields.Many2one("res.users", string="Vorschau für", copy=False)
    preview_mailbox_id = fields.Many2one(
        "mail.sync.mailbox", string="Vorschau über Postfach", domain=[("kind", "=", "shared")], copy=False
    )
    preview_html = fields.Html(compute="_compute_preview_html", sanitize=False, string="Vorschau")
    placeholder_help = fields.Html(compute="_compute_placeholder_help", sanitize=False)
    user_ids = fields.One2many("res.users", "mail_sync_signature_id", string="Benutzer")
    mailbox_ids = fields.One2many("mail.sync.mailbox", "signature_id", string="Funktionspostfächer")

    @api.depends("body_html", "preview_user_id", "preview_mailbox_id")
    @api.depends_context("uid")
    def _compute_preview_html(self):
        for template in self:
            user = template.preview_user_id or self.env.user
            template.preview_html = template._render_for(user, template.preview_mailbox_id)

    def _compute_placeholder_help(self):
        rows = Markup("").join(
            Markup("<tr><td><code>{{%s}}</code></td><td>%s</td></tr>") % (name, label)
            for name, label in signature_service.PLACEHOLDERS
        )
        for template in self:
            template.placeholder_help = Markup('<table class="table table-sm">%s</table>') % rows

    # ------------------------------------------------------------------ rendering
    @api.model
    def _base_url(self):
        # sudo: public configuration values
        icp = self.env["ir.config_parameter"].sudo()
        return (icp.get_param("mail_sync.signature_base_url") or icp.get_param("web.base.url") or "").rstrip("/")

    @api.model
    def _public_image_url(self, attachment):
        attachment = attachment.sudo()  # sudo: only the id/name/checksum of a public attachment are read
        name = re.sub(r"[^A-Za-z0-9._-]", "_", attachment.name or "bild.png")
        return f"{self._base_url()}/web/image/{attachment.id}/{name}?unique={(attachment.checksum or '')[:8]}"

    @api.model
    def _store_public_image(self, attachment, datas_b64, name, mimetype="image/png"):
        """Create or update a public image attachment and return it. ``attachment`` may be empty."""
        # sudo: signature images must be readable by anonymous recipients (public=True); only the
        # signature code creates them, with content the caller already had access to.
        Attachment = self.env["ir.attachment"].sudo()
        if attachment and attachment.exists():
            # same digest as ir.attachment.checksum, not a security use
            checksum = hashlib.sha1(base64.b64decode(datas_b64), usedforsecurity=False).hexdigest()
            if attachment.checksum != checksum:
                attachment.write({"datas": datas_b64, "name": name, "mimetype": mimetype})
            return attachment
        return Attachment.create(
            {
                "name": name,
                "datas": datas_b64,
                "mimetype": mimetype,
                "public": True,
                "res_model": PUBLIC_RES_MODEL,
                "res_id": 0,
            }
        )

    @api.model
    def _make_html_images_public(self, body_html, allow_any=False):
        """Rewrite <img> sources to public absolute URLs (editor uploads and data: URIs).

        ``allow_any``: managers may publish any attachment they can read; normal users only
        attachments they uploaded themselves (prevents publishing other people's files).
        """
        if not body_html:
            return body_html
        Attachment = self.env["ir.attachment"]

        def resolve(src):
            data_match = _DATA_URI_RE.match(src)
            if data_match:
                mimetype, payload = data_match.groups()
                try:
                    base64.b64decode(payload, validate=True)
                except (binascii.Error, ValueError):
                    return None
                ext = mimetype.split("/")[-1].replace("jpeg", "jpg").replace("svg+xml", "svg")
                attachment = self._store_public_image(Attachment, payload, f"signatur.{ext}", mimetype)
                return self._public_image_url(attachment)
            attachment_id = signature_service.attachment_id_from_src(src)
            if not attachment_id:
                return None
            attachment = Attachment.browse(attachment_id).exists()
            if not attachment:
                return None
            attachment.check_access("read")
            if not allow_any and attachment.sudo().create_uid != self.env.user:
                raise AccessError(
                    self.env._("Es können nur selbst hochgeladene Bilder in Signaturen verwendet werden.")
                )
            if not attachment.sudo().public:
                # sudo: see _store_public_image
                attachment.sudo().write({"public": True})
            return self._public_image_url(attachment)

        return signature_service.rewrite_image_sources(body_html, resolve)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("body_html"):
                vals["body_html"] = self._make_html_images_public(vals["body_html"], allow_any=True)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("body_html"):
            vals["body_html"] = self._make_html_images_public(vals["body_html"], allow_any=True)
        res = super().write(vals)
        if "body_html" in vals or "active" in vals:
            self._apply_to_users()
        return res

    def _apply_to_users(self):
        users = self.env["res.users"].sudo().search([("mail_sync_signature_managed", "=", True)])
        users._mail_sync_apply_signature()

    def _values_for(self, user, mailbox=None):
        """Placeholder values for ``user`` (sending person) and optional shared ``mailbox``."""
        user = user.sudo()  # sudo: signature data of colleagues (name, phone, photo) is shown to recipients anyway
        partner = user.partner_id
        company = (mailbox.company_id if mailbox else False) or user.company_id or self.env.company
        company = company.sudo()
        first = getattr(partner, "firstname", False)
        last = getattr(partner, "lastname", False)
        if not first and not last and partner.name:
            first, _sep, last = partner.name.partition(" ")
        department = getattr(partner, "department_id", False)
        website = (company.website or "").strip()
        website_bare = re.sub(r"^https?://", "", website).rstrip("/")
        website_url = website if website.startswith("http") else (f"https://{website_bare}" if website_bare else "")
        mobile = getattr(partner, "mobile", "") or ""
        values = {
            "name": partner.name or user.name or "",
            "first_name": first or "",
            "last_name": last or "",
            "function": partner.function or "",
            "department": department.name if department else "",
            "email": user.email or partner.email or "",
            "phone": partner.phone or "",
            "phone_link": signature_service.tel_link(partner.phone),
            "mobile": mobile,
            "mobile_link": signature_service.tel_link(mobile),
            "photo_url": user._mail_sync_photo_url(),
            # field is sanitised on write (_sanitize_user_html)
            "extra": Markup(user.mail_sync_signature_extra or ""),  # noqa: S704
            "mailbox_name": (mailbox.sender_name or mailbox.display_name or "") if mailbox else "",
            "mailbox_email": mailbox.upn if mailbox else "",
            "company_name": company.name or "",
            "company_street": company.street or "",
            "company_zip": company.zip or "",
            "company_city": company.city or "",
            "company_phone": company.phone or "",
            "company_phone_link": signature_service.tel_link(company.phone),
            "company_email": company.email or "",
            "company_website": website_bare,
            "company_website_url": website_url,
            "company_logo_url": company._mail_sync_logo_url(),
            "company_registry": company.company_registry or "",
            "company_vat": company.vat or "",
        }
        return values

    def _render_for(self, user, mailbox=None):
        self.ensure_one()
        if not self.body_html:
            return Markup("")
        return signature_service.render(self.body_html, self._values_for(user, mailbox))

    @api.model
    def _sanitize_user_html(self, body_html):
        """User-maintained snippet: sanitised and with public image URLs."""
        if not body_html:
            return body_html
        cleaned = html_sanitize(body_html, sanitize_attributes=True, sanitize_form=True)
        return self._make_html_images_public(cleaned, allow_any=False)
