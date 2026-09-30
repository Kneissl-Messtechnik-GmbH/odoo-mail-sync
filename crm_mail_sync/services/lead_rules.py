# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Rules D1–D7: which opportunity a conversation belongs to. Plugs into the core Router."""

import logging
import re
from datetime import timedelta

from odoo import fields
from odoo.addons.mail_sync_microsoft.services import router as core_router

_logger = logging.getLogger(__name__)

REPLY_PREFIX_RE = re.compile(r"^\s*((aw|re|wg|fw|fwd|sv|vs|tr)\s*:\s*)+", re.IGNORECASE)
STOPWORDS = {
    "anfrage",
    "angebot",
    "bestellung",
    "info",
    "hallo",
    "guten",
    "danke",
    "und",
    "der",
    "die",
    "das",
    "für",
    "mit",
    "the",
    "and",
}


def tokens(text):
    text = REPLY_PREFIX_RE.sub("", text or "").lower()
    return {t for t in re.findall(r"[a-zäöüß0-9]{4,}", text) if t not in STOPWORDS}


class LeadRouter(core_router.Router):
    def __init__(self, mailbox):
        super().__init__(mailbox)
        self.Lead = self.env["crm.lead"]
        self.Message = self.env["mail.sync.message"]

    # ----------------------------------------------------------- hook
    def _extra_target(self, result, msg):
        lead = self._by_conversation(msg)  # D1
        if lead:
            result.reason = f"D1: Fortsetzung der Konversation auf {lead.display_name}"
            return lead
        lead = self._by_reference(msg)  # D2
        if lead:
            result.reason = f"D2: Deal-Referenz im Betreff → {lead.display_name}"
            return lead
        candidates = self._candidates(result.partners, result.companies)
        if not candidates:
            self._maybe_propose_lead(result, msg)  # D5
            return None
        if len(candidates) == 1:  # D3
            result.reason = f"D3: einzige offene Verkaufschance von {candidates.partner_id.display_name or 'Kontakt'}"
            return candidates
        best, score, runner_up = self._score(candidates, msg)  # D4
        threshold = max(self.account.score_threshold, 0)
        if best and score - runner_up >= threshold:
            result.reason = f"D4: {len(candidates)} offene Verkaufschancen, {best.display_name} mit {score} Punkten ({runner_up} für Platz 2)"
            return best
        result.reason = f"D4: {len(candidates)} offene Verkaufschancen, kein klarer Favorit → Vorschlag"
        result.extra["candidate_lead_ids"] = [(6, 0, candidates.ids)]
        return None

    # ---------------------------------------------------------- rules
    def _by_conversation(self, msg):
        if not msg.get("conversation_id"):
            return None
        row = self.Message.sudo().search(  # sudo: conversation may live in another user's mailbox
            [("conversation_id", "=", msg["conversation_id"]), ("lead_id", "!=", False)], order="id", limit=1
        )
        return row.lead_id.exists() or None

    def _by_reference(self, msg):
        pattern = self.account.deal_reference_regex
        if not pattern:
            return None
        try:
            match = re.search(pattern, msg.get("subject") or "", re.IGNORECASE)
        except re.error:
            _logger.warning("Mail Sync: ungültiges Referenzmuster %r", pattern)
            return None
        if not match:
            return None
        text = match.group(1) if match.groups() else match.group(0)
        Lead = self.Lead.with_context(active_test=False)
        hits = Lead.search([("name", "ilike", match.group(0))], limit=2)
        if len(hits) == 1:
            return hits
        if text.isdigit():
            lead = Lead.browse(int(text)).exists()
            if lead:
                return lead
        return None

    def _candidates(self, partners, companies):
        if not partners and not companies:
            return self.Lead.browse()
        partner_ids = set(partners.ids) | set(companies.ids)
        for company in companies:
            partner_ids.update(company.child_ids.ids)
        return self.Lead.search(
            [
                ("type", "=", "opportunity"),
                ("active", "=", True),
                ("partner_id", "in", list(partner_ids)),
                ("stage_id.is_won", "=", False),
                ("probability", "<", 100),
            ]
        )

    def _score(self, candidates, msg):
        subject_tokens = tokens(msg.get("subject"))
        owner = self.mailbox.owner_user_id
        recent_limit = fields.Datetime.now() - timedelta(days=14)
        newest = max(candidates, key=lambda lead: lead.create_date or fields.Datetime.now())
        scores = []
        for lead in candidates:
            score = 0
            if owner and lead.user_id == owner:
                score += self.account.score_owner
            if subject_tokens & tokens(lead.name):
                score += self.account.score_subject
            if (lead.write_date and lead.write_date >= recent_limit) or (
                lead.date_last_stage_update and lead.date_last_stage_update >= recent_limit
            ):
                score += self.account.score_activity
            if lead == newest:
                score += self.account.score_recent
            scores.append((score, lead))
        scores.sort(key=lambda s: s[0], reverse=True)
        best_score, best = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else 0
        return best, best_score, runner_up

    def _maybe_propose_lead(self, result, msg):
        mailbox = self.mailbox
        if not mailbox.is_sales_mailbox or msg.get("direction") != "inbound":
            return
        if msg.get("conversation_id") and self.Message.sudo().search_count(  # sudo: dedupe across mailboxes
            [("conversation_id", "=", msg["conversation_id"]), ("lead_proposal", "=", True)]
        ):
            return
        if mailbox.lead_proposal == "create":
            partner = result.partners[:1]
            lead = self.Lead.with_context(mail_sync_import=True).create(
                {
                    "name": msg.get("subject") or f"Anfrage von {msg.get('from_email')}",
                    "type": "opportunity",
                    "partner_id": partner.id if partner else False,
                    "email_from": msg.get("from_email") if not partner else False,
                    "team_id": mailbox.lead_team_id.id or False,
                    "user_id": mailbox.owner_user_id.id or False,
                }
            )
            result.reason = f"D5: neue Anfrage im Vertriebspostfach → Verkaufschance {lead.display_name} angelegt"
            result.extra["_created_lead"] = lead  # consumed below by route override
            return
        result.extra["lead_proposal"] = True
        result.reason = (result.reason + "; " if result.reason else "") + "D5: neue Anfrage, Lead vorgeschlagen"

    def route(self, msg):
        result = super().route(msg)
        created = result.extra.pop("_created_lead", None)
        if created is not None:
            result.target, result.state = created, "linked"
        return result


core_router.ROUTER_CLASS["cls"] = LeadRouter
