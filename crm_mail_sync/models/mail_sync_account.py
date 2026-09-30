# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import fields, models


class MailSyncAccount(models.Model):
    _inherit = "mail.sync.account"

    deal_reference_regex = fields.Char(
        string="Muster für Deal-Referenz",
        default=r"\bID[\s:#-]*(\d{3,})\b",
        help="Regulärer Ausdruck mit einer Gruppe. Ein Treffer im Betreff ordnet die Konversation der "
        "Verkaufschance zu, deren Name die Referenz enthält (oder deren ID der Zahl entspricht).",
    )
    score_owner = fields.Integer(string="Punkte: Postfach-Eigentümer ist Lead-Eigentümer", default=3)
    score_subject = fields.Integer(string="Punkte: Betreff passt zum Lead-Titel", default=2)
    score_activity = fields.Integer(string="Punkte: Lead in den letzten 14 Tagen bearbeitet", default=2)
    score_recent = fields.Integer(string="Punkte: jüngster Lead", default=1)
    score_threshold = fields.Integer(
        string="Mindestvorsprung für automatische Zuordnung",
        default=3,
        help="Bei mehreren offenen Verkaufschancen wird nur zugeordnet, wenn der beste Kandidat mindestens so viele "
        "Punkte Vorsprung hat. Sonst wird ein Vorschlag erzeugt.",
    )
    retro_days = fields.Integer(
        string="Rückwirkende Vorschläge (Tage)",
        default=30,
        help="Beim Anlegen einer Verkaufschance werden Konversationen des Kontakts aus diesem Zeitraum vorgeschlagen.",
    )
