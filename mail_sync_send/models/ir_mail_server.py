# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging

from odoo import api, fields, models, modules
from odoo.addons.mail_sync_microsoft.services import graph as graph_service
from odoo.exceptions import UserError
from odoo.tools import email_normalize

from ..services.graph_session import GraphSendSession

_logger = logging.getLogger(__name__)

GRAPH_HOST = "graph.microsoft.com"


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    smtp_authentication = fields.Selection(
        selection_add=[("mail_sync_graph", "Microsoft 365 Graph (Mail Sync)")],
        ondelete={"mail_sync_graph": "set default"},
    )
    mail_sync_mailbox_id = fields.Many2one(
        "mail.sync.mailbox",
        string="Postfach (Mail Sync)",
        ondelete="cascade",
        help="Mails mit passendem Absender werden über Microsoft Graph aus diesem Postfach gesendet "
        "und landen dort in „Gesendete Elemente“.",
    )

    @api.depends("smtp_authentication")
    def _compute_smtp_authentication_info(self):
        graph_servers = self.filtered(lambda s: s.smtp_authentication == "mail_sync_graph")
        graph_servers.smtp_authentication_info = self.env._(
            "Versand über Microsoft Graph (sendMail). Zugangsdaten kommen aus dem verknüpften Mail-Sync-Postfach; "
            "SMTP-Benutzer und Passwort werden nicht verwendet."
        )
        return super(IrMailServer, self - graph_servers)._compute_smtp_authentication_info()

    # ------------------------------------------------------------------ sending
    def _find_mail_server(self, email_from, mail_servers=None):
        """Graph servers only ever send for their own mailbox address.

        Odoo's fallbacks (steps 2–4: notification address, servers without filter, "first server
        anyway") would otherwise route arbitrary senders through a shared mailbox.
        """
        if mail_servers is None:
            mail_servers = self.sudo().search([], order="sequence")
        graph_servers = mail_servers.filtered(lambda s: s.smtp_authentication == "mail_sync_graph")
        normalized = email_normalize(email_from) if email_from else False
        if normalized:
            for server in graph_servers.filtered("active"):
                addresses = {email_normalize(a.strip()) for a in (server.from_filter or "").split(",") if a.strip()}
                if normalized in addresses:
                    return server, email_from
        return super()._find_mail_server(email_from, mail_servers=mail_servers - graph_servers)

    def _mail_sync_resolve(self, host, smtp_from, mail_server_id, allow_archived):
        """Return the graph server ``connect()`` would use, or None for normal SMTP."""
        if mail_server_id:
            server = self.sudo().browse(mail_server_id)
            if not allow_archived and not server.active:
                return None  # super() raises the proper error
        elif not host:
            server, _smtp_from = self.sudo()._find_mail_server(smtp_from)
        else:
            return None
        if server and server.smtp_authentication == "mail_sync_graph":
            return server
        return None

    def connect(
        self,
        host=None,
        port=None,
        user=None,
        password=None,
        encryption=None,
        smtp_from=None,
        ssl_certificate=None,
        ssl_private_key=None,
        smtp_debug=False,
        mail_server_id=None,
        allow_archived=False,
    ):
        server = None
        if not modules.module.current_test or self.env.context.get("mail_sync_graph_force_session"):
            server = self._mail_sync_resolve(host, smtp_from, mail_server_id, allow_archived)
        if server:
            return server._mail_sync_session(smtp_from)
        return super().connect(
            host=host,
            port=port,
            user=user,
            password=password,
            encryption=encryption,
            smtp_from=smtp_from,
            ssl_certificate=ssl_certificate,
            ssl_private_key=ssl_private_key,
            smtp_debug=smtp_debug,
            mail_server_id=mail_server_id,
            allow_archived=allow_archived,
        )

    def _mail_sync_session(self, smtp_from=None):
        self.ensure_one()
        # sudo: sending runs for any user, the mailbox credentials are admin-only
        mailbox = self.sudo().mail_sync_mailbox_id
        if not mailbox:
            raise UserError(self.env._("Der Mailserver %s ist mit keinem Postfach verknüpft.", self.name))
        if not mailbox.active or not mailbox.send_enabled:
            raise UserError(self.env._("Der Versand über das Postfach %s ist deaktiviert.", mailbox.upn))
        return GraphSendSession(mailbox._graph(), mailbox.upn, from_filter=self.from_filter, smtp_from=smtp_from)

    @api.model
    def send_email(self, message, *args, **kwargs):
        try:
            return super().send_email(message, *args, **kwargs)
        except graph_service.GraphError as exc:
            # Map Graph errors to Odoo's delivery exception so mail.mail marks the mail as failed
            # with a readable reason instead of crashing the queue.
            from odoo.addons.base.models.ir_mail_server import MailDeliveryException  # noqa: PLC0415

            _logger.info("Mail Sync: Versand über Graph fehlgeschlagen: %s", exc)
            raise MailDeliveryException(
                self.env._("Versand über Microsoft 365 fehlgeschlagen"), self.env._("Microsoft Graph: %s", exc)
            ) from exc

    def test_smtp_connection(self, autodetect_max_email_size=False):
        graph_servers = self.filtered(lambda s: s.smtp_authentication == "mail_sync_graph")
        for server in graph_servers:
            mailbox = server.sudo().mail_sync_mailbox_id  # sudo: credentials are admin-only
            if not mailbox:
                raise UserError(self.env._("Kein Postfach verknüpft."))
            try:
                mailbox._graph().list_folders(mailbox.upn)
            except graph_service.GraphError as exc:
                raise UserError(
                    self.env._("Zugriff auf %(upn)s fehlgeschlagen: %(err)s", upn=mailbox.upn, err=exc)
                ) from exc
        if graph_servers and not (self - graph_servers):
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "type": "success",
                    "message": self.env._(
                        "Postfachzugriff funktioniert. Ob Senden erlaubt ist, zeigt erst eine Testmail "
                        "(Exchange-Rolle „Application Mail.Send“ bzw. delegierte Berechtigung Mail.Send)."
                    ),
                    "sticky": False,
                },
            }
        return super(IrMailServer, self - graph_servers).test_smtp_connection(
            autodetect_max_email_size=autodetect_max_email_size
        )
