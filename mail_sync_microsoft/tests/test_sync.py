# Copyright 2026 Kneissl Messtechnik GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from unittest.mock import patch

import psycopg2.errors
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import INFO, MailSyncCase


class TestSync(MailSyncCase):
    def test_discover_folders_include_rules(self):
        self.graph.add_folder(INFO, f"{INFO}:privat", "Privat")
        self.graph.add_folder(INFO, f"{INFO}:kunden", "Kunden")
        self.graph.add_folder(INFO, f"{INFO}:bewerb", "Bewerbungen 2026")
        self.graph.add_folder(INFO, f"{INFO}:junksub", "Alt", parent=f"{INFO}:drafts")
        folders = self.discover()
        self.assertFalse(folders["Bewerbungen 2026"].include)
        self.assertFalse(folders["Alt"].include, "child of an excluded folder")
        self.assertTrue(folders["inbox"].include)
        self.assertTrue(folders["sentitems"].include)
        self.assertFalse(folders["drafts"].include)
        self.assertFalse(folders["Privat"].include)
        self.assertTrue(folders["Kunden"].include)
        # second discovery is idempotent
        self.mailbox.job_discover_folders()
        self.assertEqual(len(self.mailbox.folder_ids), 8)

    def test_pipeline_states_and_chatter(self):
        g = self.graph
        g.add_message(INFO, "inbox", "m1", "Angebot Kalibrierung", "max@musterwerk.de", [INFO], body="Bitte Angebot")
        g.add_message(INFO, "inbox", "m2", "Intern", f"kollege@{INFO.split('@')[1]}", [INFO])
        g.add_message(INFO, "inbox", "m3", "Newsletter", "news@spam.io", [INFO])
        g.add_message(INFO, "inbox", "m4", "Neue Anfrage", "neu@musterwerk.de", [INFO])
        g.add_message(INFO, "inbox", "m5", "Unbekannt", "stranger@gmail.com", [INFO])
        g.add_message(INFO, "inbox", "m7", "[PRIVAT] Arzt", "max@musterwerk.de", [INFO])
        g.add_message(
            INFO, "inbox", "m8", "Abwesenheit", "erika@solo.example", [INFO], headers={"Auto-Submitted": "auto-replied"}
        )
        g.add_message(INFO, "sentitems", "s1", "AW: Angebot", INFO, ["max@musterwerk.de"], body="Anbei")
        self.discover()
        self.sync_folder("inbox")
        self.sync_folder("sentitems")

        m1 = self.row("m1")
        self.assertEqual(m1.state, "linked")
        self.assertEqual(m1.partner_ids, self.max)
        self.assertEqual(m1.commercial_partner_ids, self.musterwerk)
        self.assertEqual((m1.model, m1.res_id), ("res.partner", self.max.id))
        self.assertTrue(m1.mail_message_id)
        self.assertEqual(m1.mail_message_id.message_type, "email")
        self.assertEqual(m1.mail_message_id.subtype_id, self.env.ref("mail.mt_note"))
        self.assertIn("Bitte Angebot", m1.mail_message_id.body)
        self.assertEqual(m1.direction, "inbound")

        self.assertEqual(self.row("m2").state, "skipped_internal")
        self.assertEqual(self.row("m3").state, "skipped_blocklist")

        m4 = self.row("m4")  # K3 domain match + auto-create under company
        self.assertEqual(m4.state, "linked")
        self.assertEqual(m4.commercial_partner_ids, self.musterwerk)
        created = self.env["res.partner"].search([("email", "=", "neu@musterwerk.de")])
        self.assertEqual(created.parent_id, self.musterwerk)
        self.assertEqual(m4.partner_ids, created)

        m5 = self.row("m5")
        self.assertEqual(m5.state, "unmatched")
        self.assertFalse(m5.mail_message_id)

        m7 = self.row("m7")
        self.assertEqual(m7.visibility, "private")
        self.assertFalse(m7.mail_message_id)

        self.assertEqual(self.row("m8").state, "skipped_automatic")

        s1 = self.row("s1")
        self.assertEqual(s1.direction, "outbound")
        self.assertEqual(s1.state, "linked")
        self.assertEqual(s1.res_id, self.max.id)

        run = self.env["mail.sync.run"].search(
            [("mailbox_id", "=", self.mailbox.id), ("kind", "=", "delta")], limit=1, order="id desc"
        )
        self.assertEqual(run.state, "done")

    def test_idempotent_and_removed(self):
        self.graph.add_message(INFO, "inbox", "m1", "Hallo", "max@musterwerk.de", [INFO])
        self.discover()
        self.sync_folder("inbox")
        count_before = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.max.id)]
        )
        self.sync_folder("inbox")  # delta with nothing new
        self.sync_folder("inbox")
        count_after = self.env["mail.message"].search_count(
            [("model", "=", "res.partner"), ("res_id", "=", self.max.id)]
        )
        self.assertEqual(count_before, count_after)
        self.assertEqual(self.env["mail.sync.message"].search_count([("mailbox_id", "=", self.mailbox.id)]), 1)
        self.graph.remove_message(INFO, "inbox", "m1")
        self.sync_folder("inbox")
        self.assertEqual(self.row("m1").state, "removed")
        self.assertTrue(self.row("m1").mail_message_id, "chatter entry is kept as archive")

    def test_reply_threading(self):
        self.graph.add_message(
            INFO, "inbox", "m1", "Frage", "max@musterwerk.de", [INFO], internet_message_id="<q1@musterwerk.de>"
        )
        self.discover()
        self.sync_folder("inbox")
        # a reply from an unknown colleague of Max continues on Max, not on the company
        self.graph.add_message(
            INFO, "inbox", "m2", "AW: Frage", "kollegin@musterwerk.de", [INFO], in_reply_to="<q1@musterwerk.de>"
        )
        self.mailbox.write({"auto_create_partner": "no"})
        self.sync_folder("inbox")
        m2 = self.row("m2")
        self.assertEqual(m2.state, "linked")
        self.assertEqual((m2.model, m2.res_id), ("res.partner", self.max.id))
        self.assertIn("D1", m2.reason)

    def test_duplicate_of_odoo_sent_mail(self):
        odoo_msg = self.max.message_post(
            body="Von Odoo gesendet", message_type="email", subtype_xmlid="mail.mt_comment"
        )
        self.graph.add_message(
            INFO, "sentitems", "s1", "Von Odoo", INFO, ["max@musterwerk.de"], internet_message_id=odoo_msg.message_id
        )
        self.discover()
        self.sync_folder("sentitems")
        s1 = self.row("s1")
        self.assertEqual(s1.mail_message_id, odoo_msg)
        self.assertEqual(s1.state, "linked")
        self.assertIn("Message-ID", s1.reason)

    def test_throttle_and_gone(self):
        self.graph.add_message(INFO, "inbox", "m1", "Hallo", "max@musterwerk.de", [INFO])
        folders = self.discover()
        self.graph.throttle_once = True
        with self.assertRaises(RetryableJobError):
            self.mailbox.job_delta(folders["inbox"].id)
        self.mailbox.job_delta(folders["inbox"].id)
        self.assertTrue(folders["inbox"].delta_link)
        self.graph.gone_once = True
        raised = None
        try:  # not assertRaises: Odoo wraps it in a savepoint and would roll the flag back
            self.mailbox.job_delta(folders["inbox"].id)
        except RetryableJobError as exc:
            raised = exc
        self.assertIsNotNone(raised)
        self.assertTrue(folders["inbox"].needs_full_resync)
        self.mailbox.job_delta(folders["inbox"].id)  # resync from start_date
        self.assertFalse(folders["inbox"].needs_full_resync)
        self.assertEqual(self.row("m1").state, "linked")

    def test_share_private_message(self):
        self.mailbox.write({"visibility_default": "private"})
        self.graph.add_message(INFO, "inbox", "m1", "Vertraulich", "max@musterwerk.de", [INFO], body="Geheim")
        self.discover()
        self.sync_folder("inbox")
        m1 = self.row("m1")
        self.assertEqual(m1.visibility, "private")
        self.assertEqual(m1.state, "linked")
        self.assertFalse(m1.mail_message_id)
        m1.action_share()
        self.assertEqual(m1.visibility, "shared")
        self.assertTrue(m1.mail_message_id)
        self.assertIn("Geheim", m1.mail_message_id.body)

    def test_attachment_cap(self):
        self.account.write({"attachment_max_mb": 1})
        big = b"x" * (1024 * 1024 + 10)
        self.graph.add_message(
            INFO,
            "inbox",
            "m1",
            "Mit Anhang",
            "max@musterwerk.de",
            [INFO],
            attachments=[{"name": "klein.pdf", "data": b"pdf"}, {"name": "gross.bin", "data": big}],
        )
        self.discover()
        self.sync_folder("inbox")
        message = self.row("m1").mail_message_id
        self.assertEqual(message.attachment_ids.mapped("name"), ["klein.pdf"])
        self.assertIn("gross.bin", message.body)

    def test_purge_unmatched(self):
        self.graph.add_message(INFO, "inbox", "m5", "Unbekannt", "stranger@gmail.com", [INFO])
        self.discover()
        self.sync_folder("inbox")
        self.account.write({"retention_days": 0})
        self.env["mail.sync.message"]._purge_unmatched()
        self.assertTrue(self.row("m5"))
        self.account.write({"retention_days": 1})
        self.env.cr.execute(
            "update mail_sync_message set create_date = now() - interval '3 days' where id = %s",
            (self.row("m5").id,),
        )
        self.env["mail.sync.message"].invalidate_model()
        self.env["mail.sync.message"]._purge_unmatched()
        self.assertFalse(self.row("m5"))

    def test_paged_rounds_continue_until_done(self):
        for i in range(5):
            self.graph.add_message(INFO, "inbox", f"p{i}", f"Seite {i}", "max@musterwerk.de", [INFO])
        folders = self.discover()
        self.graph.page_size = 2
        self.mailbox.job_delta(folders["inbox"].id)  # continuation jobs run inline (queue_job__no_delay)
        self.assertEqual(self.env["mail.sync.message"].search_count([("mailbox_id", "=", self.mailbox.id)]), 5)
        self.assertTrue(folders["inbox"].backfill_done)
        self.assertFalse((folders["inbox"].delta_link or "").startswith("next|"), "round finished with a deltaLink")
        runs = self.env["mail.sync.run"].search([("folder_id", "=", folders["inbox"].id), ("kind", "=", "delta")])
        self.assertEqual(len(runs), 3, "one run per page")

    def test_forwarded_message_attachment(self):
        self.graph.add_message(
            INFO,
            "inbox",
            "m1",
            "WG: Original",
            "max@musterwerk.de",
            [INFO],
            attachments=[{"name": "fwd.eml", "rfc822": True, "subject": "Original"}],
        )
        self.discover()
        self.sync_folder("inbox")
        row = self.row("m1")
        self.assertEqual(row.state, "linked")
        self.assertTrue(row.mail_message_id)

    @mute_logger("odoo.sql_db")  # the simulated lock failure logs a "bad query" line
    def test_concurrent_job_on_same_folder_retries(self):
        """A second job on a folder whose row is locked must retry instead of colliding."""
        self.graph.add_message(INFO, "inbox", "m1", "Hallo", "max@musterwerk.de", [INFO])
        folders = self.discover()
        real_execute = self.env.cr.execute

        def locked(query, *args, **kwargs):
            if "FOR UPDATE NOWAIT" in query:
                try:  # like a real lock failure, this leaves the transaction aborted
                    real_execute("SELECT 1/0")
                except psycopg2.Error:
                    pass
                raise psycopg2.errors.LockNotAvailable("could not obtain lock on row")
            return real_execute(query, *args, **kwargs)

        raised = None
        try:  # not assertRaises: its savepoint would hide an aborted transaction
            with patch.object(
                type(self.env.cr), "execute", autospec=True, side_effect=lambda cr, q, *a, **k: locked(q, *a, **k)
            ):
                self.mailbox.job_delta(folders["inbox"].id)
        except RetryableJobError as exc:
            raised = exc
        self.assertIsNotNone(raised)
        self.env.cr.execute("SELECT 1")  # transaction must still be usable so queue_job can postpone
        self.assertFalse(self.row("m1"))
        self.mailbox.job_delta(folders["inbox"].id)
        self.assertEqual(self.row("m1").state, "linked")


@tagged("post_install", "-at_install")
class TestSyncWithFullRegistry(MailSyncCase):
    """Runs after all modules are loaded, so optional modules such as base_vat take part."""

    def test_auto_create_contact_under_company_with_invalid_vat(self):
        """Commercial-field sync copies the company VAT to the new contact; that must not fail."""
        company = self.musterwerk
        company.with_context(no_vat_validation=True).write({"vat": "CHE-114.947.610 "})
        self.mailbox.auto_create_partner = "company"
        self.graph.add_message(INFO, "inbox", "m1", "Anfrage", "neu@musterwerk.de", [INFO])
        self.discover()
        self.sync_folder()
        self.assertEqual(self.row("m1").state, "linked", self.row("m1").reason)
        contact = self.env["res.partner"].search([("email", "=", "neu@musterwerk.de")])
        self.assertEqual(contact.parent_id, company)
