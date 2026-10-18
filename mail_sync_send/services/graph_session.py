# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""An SMTP-session look-alike that hands Odoo's outgoing messages to Microsoft Graph.

``ir.mail_server.connect()`` returns this object for servers of type ``mail_sync_graph``;
``ir.mail_server.send_email()`` then calls ``send_message()`` / ``quit()`` exactly as on
``smtplib.SMTP``. Everything Odoo does before (From rewriting, bounce handling, batching)
keeps working unchanged.
"""

from email.utils import getaddresses


class GraphSendSession:
    def __init__(self, graph_client, upn, from_filter=None, smtp_from=None):
        self.graph = graph_client
        self.upn = upn
        # attributes read by ir.mail_server._prepare_email_message
        self.from_filter = from_filter
        self.smtp_from = smtp_from
        self.sent = 0

    def send_message(self, message, smtp_from=None, smtp_to_list=None):
        """Send ``message`` (email.message.Message). Envelope recipients missing from the
        headers (Odoo moves Bcc into the envelope) are added back as Bcc, because Graph takes
        the recipients from the MIME headers only."""
        if smtp_to_list:
            visible = {
                addr.lower() for _name, addr in getaddresses(message.get_all("To", []) + message.get_all("Cc", []))
            }
            hidden = [addr for addr in smtp_to_list if addr and addr.lower() not in visible]
            if "Bcc" in message:
                del message["Bcc"]
            if hidden:
                message["Bcc"] = ", ".join(hidden)
        # Exchange sets the envelope sender itself; a foreign Return-Path only confuses it.
        if "Return-Path" in message:
            del message["Return-Path"]
        self.graph.send_mime(self.upn, message.as_bytes())
        self.sent += 1
        return {}

    # smtplib.SMTP compatibility -------------------------------------------------
    def quit(self):
        return None

    def close(self):
        return None

    def noop(self):
        return (250, b"OK")
