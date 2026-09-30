# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo.addons.mail_sync_microsoft.tests.common import INFO, MailSyncCase


class TestLeadRules(MailSyncCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.owner = cls.env["res.users"].create(
            {
                "name": "Vertrieb",
                "login": "vertrieb@test",
                "email": "vertrieb@test",
                "groups_id": [(6, 0, [cls.env.ref("sales_team.group_sale_salesman").id])],
            }
        )
        cls.other = cls.env["res.users"].create(
            {
                "name": "Kollege",
                "login": "kollege@test",
                "email": "kollege@test",
                "groups_id": [(6, 0, [cls.env.ref("sales_team.group_sale_salesman").id])],
            }
        )
        cls.mailbox.write({"owner_user_id": cls.owner.id})
        cls.Lead = cls.env["crm.lead"].with_context(mail_sync_import=True)

    def lead(self, name, partner=None, user=None, **vals):
        return self.Lead.create(
            {
                "name": name,
                "type": "opportunity",
                "partner_id": (partner or self.max).id,
                "user_id": (user or self.owner).id,
                **vals,
            }
        )

    def sync_inbox(self, *messages):
        for args, kwargs in messages:
            self.graph.add_message(INFO, "inbox", *args, **kwargs)
        self.discover()
        self.sync_folder("inbox")

    def test_d3_single_open_lead(self):
        lead = self.lead("Kalibrierung KMG Musterwerk")
        self.sync_inbox(
            (("m1", "Termin für die Kalibrierung", "max@musterwerk.de", [INFO]), {"body": "Wann passt es?"})
        )
        row = self.row("m1")
        self.assertEqual(row.state, "linked")
        self.assertEqual(row.lead_id, lead)
        self.assertIn("D3", row.reason)
        self.assertEqual((row.mail_message_id.model, row.mail_message_id.res_id), ("crm.lead", lead.id))
        self.assertEqual(lead.mail_sync_count, 1)

    def test_d4_scoring_links_clear_favourite(self):
        favourite = self.lead("Kalibrierung KMG Musterwerk", user=self.owner)
        self.lead("Ersatzteile Taster", user=self.other)
        self.sync_inbox((("m1", "AW: Kalibrierung KMG", "max@musterwerk.de", [INFO]), {}))
        row = self.row("m1")
        self.assertEqual(row.lead_id, favourite)
        self.assertIn("D4", row.reason)
        self.assertFalse(row.candidate_lead_ids)

    def test_d4_ambiguous_becomes_suggestion(self):
        l1 = self.lead("Projekt Nord", user=self.other)
        l2 = self.lead("Projekt Süd", user=self.other)
        self.sync_inbox((("m1", "Rückfrage", "max@musterwerk.de", [INFO]), {}))
        row = self.row("m1")
        self.assertFalse(row.lead_id)
        self.assertEqual(row.res_id, self.max.id, "conversation stays on the contact")
        self.assertEqual(set(row.candidate_lead_ids.ids), {l1.id, l2.id})
        self.assertTrue(row.has_candidates)
        # wizard links the conversation to the chosen lead and moves the chatter entry
        wizard = self.env["mail.sync.link.wizard"].create({"message_ids": [(6, 0, row.ids)], "lead_id": l2.id})
        wizard.action_link()
        self.assertEqual(row.lead_id, l2)
        self.assertFalse(row.candidate_lead_ids)
        self.assertEqual((row.mail_message_id.model, row.mail_message_id.res_id), ("crm.lead", l2.id))
        self.assertEqual(l2.mail_sync_count, 1)

    def test_d2_reference_in_subject(self):
        target = self.lead("Chotest Messmaschine // ID 9429", partner=self.erika)
        self.lead("Anderes Projekt", partner=self.erika)
        self.sync_inbox((("m1", "AW: Ihr Angebot ID 9429", "erika@solo.example", [INFO]), {}))
        row = self.row("m1")
        self.assertEqual(row.lead_id, target)
        self.assertIn("D2", row.reason)

    def test_d1_conversation_continues_after_won(self):
        lead = self.lead("Kalibrierung")
        self.sync_inbox(
            (
                ("m1", "Auftrag", "max@musterwerk.de", [INFO]),
                {"conversation_id": "conv-A", "internet_message_id": "<a1@musterwerk.de>"},
            )
        )
        self.assertEqual(self.row("m1").lead_id, lead)
        lead.action_set_won()
        self.lead("Neues Projekt")  # another open lead would otherwise win by D3
        self.graph.add_message(
            INFO,
            "inbox",
            "m2",
            "AW: Auftrag",
            "max@musterwerk.de",
            [INFO],
            conversation_id="conv-A",
            in_reply_to="<a1@musterwerk.de>",
        )
        self.sync_folder("inbox")
        row = self.row("m2")
        self.assertEqual(row.lead_id, lead)
        self.assertIn("D1", row.reason)

    def test_d5_lead_proposal_and_creation(self):
        self.mailbox.write({"is_sales_mailbox": True, "lead_proposal": "suggest"})
        self.sync_inbox(
            (("m1", "Anfrage Koordinatenmessgerät", "erika@solo.example", [INFO]), {"body": "Bitte Angebot"})
        )
        row = self.row("m1")
        self.assertTrue(row.lead_proposal)
        self.assertFalse(row.lead_id)
        self.assertEqual(row.res_id, self.erika.id)
        row.action_create_lead()
        self.assertTrue(row.lead_id)
        self.assertEqual(row.lead_id.partner_id, self.erika)
        self.assertFalse(row.lead_proposal)
        # automatic creation
        self.mailbox.write({"lead_proposal": "create"})
        self.graph.add_message(INFO, "inbox", "m2", "Zweite Anfrage", "stranger@firma-neu.de", [INFO])
        self.mailbox.write({"auto_create_partner": "always"})
        self.sync_folder("inbox")
        row2 = self.row("m2")
        self.assertTrue(row2.lead_id)
        self.assertIn("D5", row2.reason)
        self.assertEqual(row2.lead_id.partner_id.email, "stranger@firma-neu.de")

    def test_d6_retro_suggestion_on_new_lead(self):
        self.sync_inbox((("m1", "Erste Frage", "max@musterwerk.de", [INFO]), {"conversation_id": "conv-R"}))
        row = self.row("m1")
        self.assertFalse(row.lead_id)
        lead = self.env["crm.lead"].create(
            {"name": "Neu für Musterwerk", "type": "opportunity", "partner_id": self.max.id}
        )
        self.assertIn(lead, row.candidate_lead_ids)
        self.assertEqual(lead.mail_sync_suggestion_count, 1)
        note = lead.message_ids.filtered(lambda m: "Konversation" in (m.body or ""))
        self.assertTrue(note)
        row._link_conversation(lead)
        self.assertEqual(row.lead_id, lead)
        lead.invalidate_recordset(["mail_sync_suggestion_count", "mail_sync_count"])
        self.assertEqual(lead.mail_sync_suggestion_count, 0)
        self.assertEqual(lead.mail_sync_count, 1)

    def test_sales_user_sees_shared_rows(self):
        self.lead("Kalibrierung")
        self.sync_inbox((("m1", "Frage", "max@musterwerk.de", [INFO]), {}))
        rows = self.env["mail.sync.message"].with_user(self.other).search([("mailbox_id", "=", self.mailbox.id)])
        self.assertEqual(len(rows), 1)
