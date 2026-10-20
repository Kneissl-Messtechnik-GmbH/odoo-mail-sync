# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import models


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def message_post(self, *, message_type="notification", email_from=None, author_id=None, **kwargs):
        """Chatter messages to customers go out through the default sending mailbox.

        Only plain user messages are touched (inline chatter "Send message"): no explicit sender,
        type comment, discussion subtype, internal user. Templates and the full composer pass
        their own sender and are left alone.
        """
        if (
            email_from is None
            and author_id is None
            and message_type == "comment"
            and not self.env.context.get("mail_sync_no_identity")
            and self._mail_sync_is_discussion(kwargs)
            # the chatter controller posts with sudo(): env.user is still the real user
            and self.env.user._is_internal()
            and not self.env.user._is_superuser()
        ):
            mailbox = self.env["mail.sync.mailbox"]._mail_sync_default_for(self._name, include_personal=False)
            if mailbox and mailbox.kind == "shared":
                email_from = mailbox._mail_sync_from_address(self.env.user)
                author_id = self.env.user.partner_id.id
        return super().message_post(message_type=message_type, email_from=email_from, author_id=author_id, **kwargs)

    def _mail_sync_is_discussion(self, kwargs):
        if kwargs.get("subtype_xmlid"):
            return kwargs["subtype_xmlid"] == "mail.mt_comment"
        subtype_id = kwargs.get("subtype_id")
        return bool(subtype_id) and subtype_id == self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_comment")

    def _notify_by_email_prepare_rendering_context(self, message, msg_vals=False, **kwargs):
        """Mails through a shared mailbox carry the mailbox signature with the sending person."""
        context = super()._notify_by_email_prepare_rendering_context(message, msg_vals=msg_vals, **kwargs)
        author_user = context.get("author_user")
        if not (context.get("email_add_signature") and author_user):
            return context
        email_from = (msg_vals or {}).get("email_from") or message.email_from
        mailbox = self.env["mail.sync.mailbox"]._mail_sync_from_email(email_from)
        if mailbox and mailbox.kind == "shared":
            context["signature"] = mailbox._mail_sync_render_signature(author_user)
        return context
