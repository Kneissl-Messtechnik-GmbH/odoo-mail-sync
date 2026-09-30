# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Router: participants of a message → contacts → companies → target record.

Rules K1–K5 of the design spec. CRM rules plug in through ``_extra_target``.
"""

import logging
from dataclasses import dataclass, field

from . import prefilter as pf

_logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    state: str = "unmatched"
    reason: str = ""
    partners: object = None  # res.partner recordset (contacts and/or companies)
    companies: object = None  # res.partner recordset (commercial partners)
    target: object = None  # record the conversation is posted on, or None
    visibility: str = "shared"
    extra: dict = field(default_factory=dict)


class Router:
    def __init__(self, mailbox):
        self.mailbox = mailbox
        self.account = mailbox.account_id
        self.env = mailbox.env
        self.internal = self.account._internal_domain_list()
        self.freemail = self.account._freemail_domain_list()
        self.block_addresses, self.block_domains = mailbox._blocklist()
        self.prefixes = mailbox._subject_prefixes()

    # ------------------------------------------------------------ entry
    def route(self, msg):
        """``msg`` is the normalized message dict from ``sync.normalize_row``."""
        Partner = self.env["res.partner"]
        result = RouteResult(partners=Partner.browse(), companies=Partner.browse())
        addresses = [msg.get("from_email")] + list(msg.get("to_emails", [])) + list(msg.get("cc_emails", []))
        addresses = [a for a in addresses if a]
        if pf.is_internal(addresses, self.internal):
            result.state, result.reason = "skipped_internal", "Nur interne Adressen"
            return result
        if pf.is_blocked(addresses, self.block_addresses, self.block_domains):
            result.state, result.reason = "skipped_blocklist", "Adresse oder Domain auf der Blockliste"
            return result
        if pf.is_private(msg.get("subject"), msg.get("folder_name"), self.prefixes):
            result.visibility = "private"
            result.reason = "Als privat markiert (Betreff oder Ordner)"
        elif self.mailbox.visibility_default == "private":
            result.visibility = "private"

        external = pf.external_addresses(addresses, self.internal)
        if not external:
            result.state, result.reason = "unmatched", "Keine externe Adresse"
            return result

        partners = self._match_partners(external)  # K1
        companies = self._companies_of(partners)  # K2
        unmatched = [a for a in external if a not in set(partners.mapped("email_normalized"))]
        domain_companies = self._match_companies_by_domain(unmatched)  # K3
        companies |= domain_companies
        created = self._maybe_create_partners(unmatched, domain_companies)
        partners |= created
        companies |= self._companies_of(created)

        result.partners, result.companies = partners, companies
        if not partners and not companies:
            result.state, result.reason = "unmatched", f"Kein Kontakt für {', '.join(external[:3])}"  # K4
            return result

        extra = self._extra_target(result, msg)  # CRM hook
        if extra is not None:
            result.target = extra
            result.state = "linked"
            return result
        result.target, why = self._pick_partner_target(partners, companies)
        result.state = "linked"
        result.reason = result.reason and f"{result.reason}; {why}" or why
        return result

    # ------------------------------------------------------------ steps
    def _match_partners(self, external):
        Partner = self.env["res.partner"]
        if not external:
            return Partner.browse()
        return Partner.search([("email_normalized", "in", external)])

    @staticmethod
    def _companies_of(partners):
        return partners.mapped("commercial_partner_id").filtered("is_company")

    def _match_companies_by_domain(self, addresses):
        """K3: unknown address at a known company domain → that company (never freemail)."""
        Partner = self.env["res.partner"]
        companies = Partner.browse()
        domains = {pf.split_domain(a) for a in addresses} - set(self.freemail) - set(self.internal)
        for domain in sorted(d for d in domains if d):
            hit = Partner.search(
                ["|", ("email_normalized", "=like", f"%@{domain}"), ("website", "ilike", domain)], limit=20
            ).mapped("commercial_partner_id")
            hit = hit.filtered(
                lambda p, d=domain: p.is_company and (not p.website or d in (p.website or "").lower() or True)
            )
            if len(hit) == 1:
                companies |= hit
        return companies

    def _maybe_create_partners(self, addresses, domain_companies):
        Partner = self.env["res.partner"]
        mode = self.mailbox.auto_create_partner
        created = Partner.browse()
        if mode == "no" or not addresses:
            return created
        for address in addresses:
            if pf.is_freemail(address, self.freemail) and mode != "always":
                continue
            domain = pf.split_domain(address)
            company = domain_companies.filtered(
                lambda c, d=domain: d and (d in (c.email_normalized or "") or d in (c.website or "").lower())
            )[:1]
            if mode == "company" and not company:
                continue
            created |= Partner.create(
                {
                    "name": address.split("@", 1)[0].replace(".", " ").title(),
                    "email": address,
                    "parent_id": company.id if company else False,
                    "type": "contact",
                    "company_type": "person",
                }
            )
        return created

    @staticmethod
    def _pick_partner_target(partners, companies):
        contacts = partners.filtered(lambda p: not p.is_company)
        if len(contacts) == 1:
            return contacts, "K1: Kontakt per E-Mail-Adresse"
        if len(companies) == 1:
            return companies, "K2/K3: Firma des Kontakts bzw. der Domain"
        if contacts:
            return contacts[:1], f"K1: erster von {len(contacts)} Kontakten"
        return (companies[:1] or partners[:1]), "Erster Treffer"

    # ------------------------------------------------------------ hook
    def _extra_target(self, result, msg):
        """Return a record to post the conversation on (CRM rules), or None."""
        return None


# Extensions register a subclass here (crm_mail_sync does).
ROUTER_CLASS = {"cls": Router}


def get_router(mailbox):
    return ROUTER_CLASS["cls"](mailbox)
