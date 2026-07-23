from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Claim, Reminder, Vehicle, compliance_state


class ClaimModelTests(TestCase):
    def test_reference_sequence(self):
        a = Claim.objects.create()
        b = Claim.objects.create()
        self.assertTrue(a.reference.startswith("CLM-"))
        self.assertEqual(
            int(b.reference.rsplit("-", 1)[1]),
            int(a.reference.rsplit("-", 1)[1]) + 1,
        )

    def test_submit_promotes_draft(self):
        claim = Claim.objects.create()
        self.assertTrue(claim.is_draft)
        claim.submit()
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.OPEN)
        self.assertIsNotNone(claim.submitted_at)

    def test_history_recorded(self):
        claim = Claim.objects.create()
        claim.vehicle_registration = "ECB-001"
        claim.save()
        self.assertEqual(claim.history.count(), 2)


class ViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="pw")
        self.client.force_login(self.user)

    def test_dashboard(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_claim_new_creates_draft_and_redirects(self):
        response = self.client.post(reverse("claim_new"))
        claim = Claim.objects.get()
        self.assertRedirects(response, reverse("claim_edit", args=[claim.pk]))
        self.assertTrue(claim.is_draft)

    def test_autosave_updates_draft(self):
        claim = Claim.objects.create(created_by=self.user)
        response = self.client.post(
            reverse("claim_autosave", args=[claim.pk]),
            {"vehicle_registration": "ECB-777", "fault": "unknown"},
        )
        self.assertEqual(response.status_code, 200)
        claim.refresh_from_db()
        self.assertEqual(claim.vehicle_registration, "ECB-777")
        self.assertTrue(claim.is_draft)

    def test_search_returns_partial_for_htmx(self):
        Claim.objects.create(vehicle_registration="ECB-999", created_by=self.user)
        response = self.client.get(
            reverse("claim_list"), {"q": "ECB-999"}, headers={"HX-Request": "true"}
        )
        self.assertContains(response, "ECB-999")
        self.assertNotContains(response, "<html")

    def test_tab_partial(self):
        claim = Claim.objects.create(created_by=self.user)
        response = self.client.get(
            reverse("claim_tab", args=[claim.pk, "billing"]),
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.status_code, 200)

    def test_reminder_toggle(self):
        claim = Claim.objects.create(created_by=self.user)
        reminder = Reminder.objects.create(
            claim=claim, title="Chase insurer", due_at="2026-01-01T10:00:00Z"
        )
        self.client.post(reverse("reminder_toggle", args=[reminder.pk]))
        reminder.refresh_from_db()
        self.assertTrue(reminder.is_done)

    def test_recovery_totals(self):
        from decimal import Decimal

        claim = Claim.objects.create(
            labour_amount=Decimal("243.60"),
            spray_material_amount=Decimal("429.53"),
            parts_amount=Decimal("800.21"),
            loe_days=4,
            loe_daily_rate=Decimal("46.405"),
            amount_paid=Decimal("1023.34"),
            offset_amount=Decimal("240.00"),
        )
        self.assertEqual(claim.loss_of_earnings, Decimal("185.62"))
        self.assertEqual(claim.total_claim_amount, Decimal("1658.96"))
        self.assertEqual(claim.outstanding_amount, Decimal("395.62"))

    def test_search_by_tp_claim_number(self):
        Claim.objects.create(tp_claim_number="C34-277148", created_by=self.user)
        response = self.client.get(
            reverse("claim_list"), {"q": "C34-277148"}, headers={"HX-Request": "true"}
        )
        self.assertContains(response, "CLM-")

    def test_urgent_claim_on_dashboard_chasers(self):
        claim = Claim.objects.create(
            status=Claim.Status.OPEN,
            vehicle_registration="ECB-800",
            urgent=True,
            next_action="O/S payment from MSI",
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, claim.reference)
        self.assertContains(response, "O/S payment from MSI")

    def test_vehicle_add_retire_delete(self):
        response = self.client.post(
            reverse("vehicle_add"),
            {"registration": "ECB-500", "make_model": "Toyota Yaris"},
        )
        self.assertRedirects(response, reverse("vehicle_list"))
        vehicle = Vehicle.objects.get(registration="ECB-500")
        self.assertTrue(vehicle.is_active)

        self.client.post(reverse("vehicle_toggle_status", args=[vehicle.pk]))
        vehicle.refresh_from_db()
        self.assertFalse(vehicle.is_active)
        self.assertIsNotNone(vehicle.retired_on)

        self.client.post(reverse("vehicle_delete", args=[vehicle.pk]))
        self.assertFalse(Vehicle.objects.filter(pk=vehicle.pk).exists())

    def test_compliance_states_and_dashboard_reminder(self):
        today = timezone.localdate()
        self.assertEqual(compliance_state(today - timedelta(days=1)), "overdue")
        self.assertEqual(compliance_state(today + timedelta(days=10)), "soon")
        self.assertEqual(compliance_state(today + timedelta(days=90)), "ok")
        self.assertEqual(compliance_state(None), "")

        Vehicle.objects.create(
            registration="ECB-600", insurance_due=today + timedelta(days=5)
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "ECB-600")
        self.assertContains(response, "due soon")

    def test_retired_vehicle_not_in_compliance_list(self):
        today = timezone.localdate()
        Vehicle.objects.create(
            registration="ECB-700",
            status=Vehicle.Status.RETIRED,
            insurance_due=today - timedelta(days=5),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertNotContains(response, "ECB-700")

    def test_home_page_tiles(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "New claim")
        self.assertContains(response, "Reminders")

    def test_standalone_reminder(self):
        response = self.client.post(
            reverse("reminder_create"),
            {"title": "Call surveyor", "due_at": "2026-08-01T10:00"},
        )
        self.assertRedirects(response, reverse("reminder_list"))
        reminder = Reminder.objects.get(title="Call surveyor")
        self.assertIsNone(reminder.claim)
        self.assertFalse(reminder.is_auto)

    def test_auto_reminder_for_insurance_and_chase(self):
        from .services.auto_reminders import sync_auto_reminders

        today = timezone.localdate()
        vehicle = Vehicle.objects.create(
            registration="ECB-810", insurance_due=today + timedelta(days=10)
        )
        claim = Claim.objects.create(
            status=Claim.Status.OPEN,
            chase_on=today + timedelta(days=3),
            next_action="O/S payment from MSI",
        )
        sync_auto_reminders()
        sync_auto_reminders()  # idempotent — must not duplicate
        autos = Reminder.objects.filter(is_auto=True)
        self.assertEqual(autos.count(), 2)
        self.assertTrue(autos.filter(title__contains="ECB-810", claim__isnull=True).exists())
        self.assertTrue(autos.filter(claim=claim, title__contains="O/S payment").exists())

        # Date moves on -> stale auto reminder replaced, not duplicated.
        vehicle.insurance_due = today + timedelta(days=40)
        vehicle.save()
        sync_auto_reminders()
        self.assertEqual(Reminder.objects.filter(is_auto=True).count(), 2)

    def test_bills_report_month_and_csv(self):
        from datetime import date
        from decimal import Decimal

        Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-BILL",
            third_party_insurer="Mapfre Middlesea",
            bills_sent_on=date(2026, 7, 5), invoice_number="100050",
            labour_amount=Decimal("100.00"), parts_amount=Decimal("50.00"),
            created_by=self.user,
        )
        Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-OTHER",
            bills_sent_on=date(2026, 6, 20), created_by=self.user,
        )
        r = self.client.get(reverse("bills_report"), {"month": "2026-07"})
        self.assertContains(r, "ECB-BILL")
        self.assertNotContains(r, "ECB-OTHER")
        self.assertContains(r, "July 2026")
        self.assertContains(r, "Mapfre Middlesea")  # grouped by insurer
        self.assertContains(r, "150.00")  # total for the month

        csv = self.client.get(reverse("bills_report"), {"month": "2026-07", "format": "csv"})
        self.assertEqual(csv["Content-Type"], "text/csv")
        self.assertIn("ECB-BILL", csv.content.decode())
        self.assertIn("100050", csv.content.decode())

    def test_claim_group_tables(self):
        from datetime import timedelta

        today = timezone.localdate()
        open_c = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-OPN", created_by=self.user
        )
        closed_c = Claim.objects.create(
            status=Claim.Status.CLOSED, vehicle_registration="ECB-CLS", created_by=self.user
        )
        overdue_c = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-OVD",
            chase_on=today - timedelta(days=3), created_by=self.user,
        )

        r = self.client.get(reverse("claim_group", args=["open"]))
        self.assertContains(r, "ECB-OPN")
        self.assertContains(r, "ECB-OVD")  # overdue is also open
        self.assertNotContains(r, "ECB-CLS")

        r = self.client.get(reverse("claim_group", args=["closed"]))
        self.assertContains(r, "ECB-CLS")
        self.assertNotContains(r, "ECB-OPN")

        r = self.client.get(reverse("claim_group", args=["overdue"]))
        self.assertContains(r, "ECB-OVD")
        self.assertNotContains(r, "ECB-OPN")

        self.assertEqual(
            self.client.get(reverse("claim_group", args=["bogus"])).status_code, 400
        )

    def test_invoice_company_selection(self):
        from decimal import Decimal

        from .models import Company

        co = Company.objects.create(
            name="Vai Drive Co Ltd.", address="Paceville, Malta",
            bank_name="Banif Bank", iban="MT40BNIF...",
            logo_static="img/companies/vai.png",
        )
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-INV",
            third_party_registration="ABC123", third_party_insurer="Mapfre",
            tp_claim_number="C34-999", labour_amount=Decimal("100.00"),
            parts_amount=Decimal("250.00"), loe_days=3,
            loe_daily_rate=Decimal("35.00"), created_by=self.user,
        )
        # No company chosen yet -> prompt, no summary.
        r = self.client.get(reverse("claim_documents", args=[claim.pk]), {"doc": "statement"})
        self.assertContains(r, "Select a company")
        self.assertNotContains(r, "Total NET")
        # Company chosen -> statement summary with letterhead + total.
        r = self.client.get(
            reverse("claim_documents", args=[claim.pk]),
            {"doc": "statement", "company": co.pk},
        )
        self.assertContains(r, "Vai Drive Co Ltd.")
        self.assertContains(r, "455.00")  # 100 + 250 + 3*35
        self.assertContains(r, "Total NET")

    def test_maintenance_rates(self):
        from decimal import Decimal

        from .models import DailyRate

        # Add via the maintenance page
        self.client.post(reverse("maintenance"), {"name": "Van", "amount": "45.00"})
        rate = DailyRate.objects.get(name="Van")
        self.assertEqual(rate.amount, Decimal("45.00"))
        # Edit
        self.client.post(
            reverse("rate_edit", args=[rate.pk]),
            {"name": "Van (large)", "amount": "50.00", "is_active": "on"},
        )
        rate.refresh_from_db()
        self.assertEqual(rate.name, "Van (large)")
        self.assertEqual(rate.amount, Decimal("50.00"))
        # The claim form offers the maintained rate
        claim = Claim.objects.create(created_by=self.user)
        r = self.client.get(reverse("claim_edit", args=[claim.pk]))
        self.assertContains(r, "Or pick a saved rate")
        self.assertContains(r, "Van (large)")
        # Delete
        self.client.post(reverse("rate_delete", args=[rate.pk]))
        self.assertFalse(DailyRate.objects.filter(pk=rate.pk).exists())

    def test_other_charges_lines(self):
        from decimal import Decimal

        claim = Claim.objects.create(created_by=self.user)
        # Autosave posts two "other" lines
        self.client.post(
            reverse("claim_autosave", args=[claim.pk]),
            {
                "fault": "unknown",
                "other_desc": ["Wheel alignment", "Windscreen repair", ""],
                "other_amt": ["45.00", "120.50", ""],
            },
        )
        claim.refresh_from_db()
        self.assertEqual(claim.other_charges.count(), 2)  # blank row ignored
        self.assertEqual(claim.total_claim_amount, Decimal("165.50"))
        # They appear as invoice lines with their descriptions
        items = [line[0] for line in claim.invoice_lines()]
        self.assertIn("Wheel alignment", items)
        self.assertIn("Windscreen repair", items)
        # Re-posting replaces, not appends
        self.client.post(
            reverse("claim_autosave", args=[claim.pk]),
            {"fault": "unknown", "other_desc": ["Towing"], "other_amt": ["80.00"]},
        )
        claim.refresh_from_db()
        self.assertEqual(claim.other_charges.count(), 1)
        self.assertEqual(claim.total_claim_amount, Decimal("80.00"))

    def test_repairs_invoice_pdf(self):
        from decimal import Decimal

        from .models import Company

        co = Company.objects.create(
            name="eCabs Ltd", address="St Julians, Malta",
            vat_no="MT21583611", exo_number="4270",
            logo_static="img/companies/ecabs.png",
        )
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="BLY438",
            tp_claim_number="M24109061", labour_amount=Decimal("200.00"),
            spray_material_amount=Decimal("168.02"), created_by=self.user,
        )
        # net = 368.02, vat @18% = 66.24, total = 434.26
        self.assertEqual(claim.repairs_total, Decimal("368.02"))
        r = self.client.get(
            reverse("claim_documents", args=[claim.pk]),
            {"doc": "repairs", "company": co.pk},
        )
        self.assertContains(r, "VAT @18%")
        self.assertContains(r, "66.24")
        self.assertContains(r, "434.26")
        pdf = self.client.get(reverse("claim_repairs_pdf", args=[claim.pk]), {"company": co.pk})
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content[:5] == b"%PDF-")

    def test_loss_of_use_letter_pdf(self):
        from decimal import Decimal

        from .models import Company

        co = Company.objects.create(
            name="eCabs Ltd", address="St Julians, Malta",
            email="motorclaims@ecabs.com.mt", logo_static="img/companies/ecabs.png",
        )
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="BLY438",
            vehicle_make_model="Peugeot 208", third_party_registration="GCF078",
            tp_claim_number="M24109061", loe_days=3,
            loe_daily_rate=Decimal("159.58"), created_by=self.user,
        )
        # Documents page (loss-of-earnings summary)
        r = self.client.get(
            reverse("claim_documents", args=[claim.pk]),
            {"doc": "lossofuse", "company": co.pk},
        )
        self.assertContains(r, "Loss of use")
        self.assertContains(r, "3 days")
        # PDF
        r = self.client.get(reverse("claim_lou_pdf", args=[claim.pk]), {"company": co.pk})
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content[:5] == b"%PDF-")

    def test_invoice_pdf(self):
        from decimal import Decimal

        from .models import Company

        co = Company.objects.create(
            name="Vai Drive Co Ltd.", address="Paceville, Malta",
            bank_name="Banif Bank", logo_static="img/companies/vai.png",
        )
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-PDF",
            labour_amount=Decimal("100.00"), created_by=self.user,
        )
        r = self.client.get(
            reverse("claim_invoice_pdf", args=[claim.pk]), {"company": co.pk}
        )
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r["Content-Disposition"].startswith("inline"))
        self.assertTrue(r.content[:5] == b"%PDF-")

    def test_data_dashboard(self):
        Claim.objects.create(status=Claim.Status.OPEN, created_by=self.user)
        r = self.client.get(reverse("data_dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Claims by status")
        self.assertContains(r, "chartData")

    def test_bills_pending_by_insurer(self):
        from datetime import date
        from decimal import Decimal

        # Billed, still outstanding
        Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-PEND",
            third_party_insurer="Elmo Insurance",
            bills_sent_on=date(2026, 5, 1), parts_amount=Decimal("500.00"),
            created_by=self.user,
        )
        # Billed but fully paid -> not pending
        Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-PAID",
            third_party_insurer="Atlas",
            bills_sent_on=date(2026, 5, 1), parts_amount=Decimal("100.00"),
            amount_paid=Decimal("100.00"), created_by=self.user,
        )
        r = self.client.get(reverse("bills_report"))
        self.assertContains(r, "Bills pending")
        self.assertContains(r, "ECB-PEND")
        self.assertNotContains(r, "ECB-PAID")
        # Filter by a different insurer hides it
        r = self.client.get(reverse("bills_report"), {"pending_insurer": "GasanMamo"})
        self.assertNotContains(r, "ECB-PEND")

    def test_workflow_survey_liability_to_chasing(self):
        from datetime import date

        claim = Claim.objects.create(
            status=Claim.Status.AWAITING_SURVEY, created_by=self.user
        )
        # Not yet: survey received but liability not accepted
        claim.survey_in_hand = True
        claim.run_workflow()
        self.assertEqual(claim.status, Claim.Status.AWAITING_SURVEY)
        # Survey received + liability accepted -> chasing (awaiting insurer)
        claim.liability = Claim.Liability.ACCEPTED
        claim.run_workflow()
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.AWAITING_INSURER)
        self.assertIsNotNone(claim.chase_on)

    def test_workflow_closes_when_fully_paid(self):
        from decimal import Decimal

        claim = Claim.objects.create(
            status=Claim.Status.AWAITING_INSURER,
            parts_amount=Decimal("500.00"), created_by=self.user,
        )
        claim.run_workflow()  # outstanding 500 -> stays open
        self.assertEqual(claim.status, Claim.Status.AWAITING_INSURER)
        claim.amount_paid = Decimal("200.00")
        claim.run_workflow()  # still 300 outstanding -> stays open
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.AWAITING_INSURER)
        claim.amount_paid = Decimal("500.00")
        claim.run_workflow()  # fully paid -> closed
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.CLOSED)

    def test_full_automation_cycle(self):
        """Walk one claim through the whole lifecycle end-to-end."""
        from datetime import timedelta
        from decimal import Decimal

        from .services.auto_reminders import sync_auto_reminders

        today = timezone.localdate()

        def autos(text):
            return Reminder.objects.filter(
                is_auto=True, claim=claim, title__contains=text
            ).exists()

        # 1. Open the claim
        claim = Claim.objects.create(created_by=self.user)
        claim.submit()
        self.assertEqual(claim.status, Claim.Status.OPEN)

        # 2. Book a survey 2 days out -> day-before reminder
        claim.survey_booked = True
        claim.survey_date = today + timedelta(days=2)
        claim.save()
        sync_auto_reminders()
        self.assertTrue(autos("Survey tomorrow"))

        # 3. Survey date passes, still not received -> chase-survey reminder
        claim.survey_date = today - timedelta(days=1)
        claim.save()
        sync_auto_reminders()
        self.assertTrue(autos("Chase survey report"))
        self.assertFalse(autos("Survey tomorrow"))  # old one cleared

        # 4. Survey received but liability disputed -> monthly liability chase, still open
        claim.survey_in_hand = True
        claim.liability = Claim.Liability.DISPUTED
        claim.save()
        claim.run_workflow()
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.OPEN)
        sync_auto_reminders()
        self.assertTrue(autos("Chase liability"))

        # 5. Liability accepted -> claim becomes a chasing claim
        claim.liability = Claim.Liability.ACCEPTED
        claim.save()
        claim.run_workflow()
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.AWAITING_INSURER)
        self.assertIsNotNone(claim.chase_on)

        # 6. Bills sent with an amount outstanding -> monthly payment chase
        claim.bills_sent_on = today
        claim.parts_amount = Decimal("500.00")
        claim.insurer_contact = "MSI claims — 2123 4567"
        claim.save()
        sync_auto_reminders()
        self.assertTrue(autos("Chase payment"))

        # 7. Partial payment -> every cent still open
        claim.amount_paid = Decimal("200.00")
        claim.save()
        claim.run_workflow()
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.AWAITING_INSURER)

        # 8. Fully paid -> auto-closed
        claim.amount_paid = Decimal("500.00")
        claim.save()
        claim.run_workflow()
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.Status.CLOSED)
        self.assertEqual(claim.outstanding_amount, Decimal("0.00"))

    def test_invoice_date_is_manual(self):
        import os
        import tempfile
        from datetime import date
        from decimal import Decimal

        import fitz

        from .models import Company

        co = Company.objects.create(
            name="Vai Drive Co Ltd.", address="Malta", logo_static="img/companies/vai.png"
        )
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, bills_sent_on=date(2026, 5, 20),
            parts_amount=Decimal("100.00"), created_by=self.user,
        )
        # The documents page offers a manual date input and does NOT auto-fill
        # the invoice date from bills_sent_on.
        r = self.client.get(
            reverse("claim_documents", args=[claim.pk]),
            {"doc": "statement", "company": co.pk},
        )
        self.assertContains(r, "Invoice date")
        self.assertContains(r, 'name="date"')
        self.assertNotContains(r, "20/05/2026")

        def pdf_text(params):
            resp = self.client.get(reverse("claim_invoice_pdf", args=[claim.pk]), params)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(resp.content)
                path = f.name
            t = fitz.open(path).load_page(0).get_text().replace("\n", " ")
            os.unlink(path)
            return t

        # Typed date appears; no date -> dash, never today.
        self.assertIn("Invoice date: 07/06/2026", pdf_text({"company": co.pk, "date": "2026-06-07"}))
        self.assertIn("Invoice date: —", pdf_text({"company": co.pk}))

    def test_workflow_reminders(self):
        from datetime import timedelta

        from .services.auto_reminders import sync_auto_reminders

        today = timezone.localdate()
        # Survey booked in 3 days -> day-before reminder
        c1 = Claim.objects.create(
            status=Claim.Status.AWAITING_SURVEY, survey_booked=True,
            survey_date=today + timedelta(days=3), created_by=self.user,
        )
        # Liability disputed -> monthly chase
        c2 = Claim.objects.create(
            status=Claim.Status.OPEN, liability=Claim.Liability.DISPUTED,
            third_party_insurer="Elmo", created_by=self.user,
        )
        sync_auto_reminders()
        sync_auto_reminders()  # idempotent
        titles = list(Reminder.objects.filter(is_auto=True).values_list("title", flat=True))
        self.assertTrue(any("Survey tomorrow" in t for t in titles))
        self.assertTrue(any("Chase liability" in t for t in titles))
        self.assertEqual(
            Reminder.objects.filter(is_auto=True, title__contains="Survey tomorrow").count(), 1
        )

    def test_open_claims_estimate(self):
        from decimal import Decimal

        claim = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-EST", created_by=self.user
        )
        r = self.client.get(reverse("claim_group", args=["open"]))
        self.assertContains(r, "Estimated total bill")
        self.assertContains(r, "Estimate")
        # Inline-save an estimate
        self.client.post(
            reverse("claim_set_estimate", args=[claim.pk]),
            {"estimate_amount": "1500.00", "next": reverse("claim_group", args=["open"])},
        )
        claim.refresh_from_db()
        self.assertEqual(claim.estimate_amount, Decimal("1500.00"))

    def test_claim_sheets_sort(self):
        from datetime import date

        old = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-OLD",
            accident_date=date(2025, 1, 5), created_by=self.user,
        )
        new = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-NEW",
            accident_date=date(2026, 6, 20), created_by=self.user,
        )
        body = self.client.get(
            reverse("claim_group", args=["open"]), {"sort": "date_asc"}
        ).content.decode()
        self.assertLess(body.index("ECB-OLD"), body.index("ECB-NEW"))
        body = self.client.get(
            reverse("claim_group", args=["open"]), {"sort": "date_desc"}
        ).content.decode()
        self.assertLess(body.index("ECB-NEW"), body.index("ECB-OLD"))
        # Claims list accepts the sort param too.
        self.assertEqual(
            self.client.get(reverse("claim_list"), {"sort": "case"}).status_code, 200
        )

    def test_case_number_assigned_on_submit(self):
        a = Claim.objects.create()
        self.assertIsNone(a.case_number)
        self.assertEqual(a.case_ref, "")
        a.submit()
        a.refresh_from_db()
        self.assertEqual(a.case_number, 1)
        self.assertEqual(a.case_ref, "VD-00001")
        # Second claim gets the next running number; never resets.
        b = Claim.objects.create(status=Claim.Status.OPEN)
        self.assertEqual(b.case_number, 2)

    def test_master_sheet_grouped_by_month_and_summary(self):
        from datetime import date

        Claim.objects.create(
            status=Claim.Status.OPEN, accident_date=date(2026, 3, 15),
            created_by=self.user,
        )
        Claim.objects.create(
            status=Claim.Status.CLOSED, accident_date=date(2025, 11, 2),
            created_by=self.user,
        )
        response = self.client.get(reverse("master_sheet"))
        self.assertContains(response, "March 2026")
        self.assertContains(response, "November 2025")
        self.assertContains(response, "Claims by status")
        # Newest month appears before the older one in the page.
        body = response.content.decode()
        self.assertLess(body.index("March 2026"), body.index("November 2025"))

    def test_master_overdue_flag_filter(self):
        from datetime import timedelta

        today = timezone.localdate()
        overdue = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-OVR",
            chase_on=today - timedelta(days=2), created_by=self.user,
        )
        Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="ECB-OK",
            chase_on=today + timedelta(days=5), created_by=self.user,
        )
        response = self.client.get(reverse("master_sheet"), {"flag": "overdue"})
        self.assertContains(response, "ECB-OVR")
        self.assertNotContains(response, "ECB-OK")

    def test_vehicle_add_page(self):
        response = self.client.get(reverse("vehicle_add"))
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("vehicle_add"),
            {"registration": "ECB-NEW", "make_model": "Kia Picanto"},
        )
        self.assertRedirects(response, reverse("vehicle_list"))
        self.assertTrue(Vehicle.objects.filter(registration="ECB-NEW").exists())

    def test_master_sheet_and_csv(self):
        from decimal import Decimal

        claim = Claim.objects.create(
            vehicle_registration="ECB-850",
            labour_amount=Decimal("100.00"),
            parts_amount=Decimal("50.00"),
            created_by=self.user,
        )
        response = self.client.get(reverse("master_sheet"))
        self.assertContains(response, claim.reference)
        self.assertContains(response, "150.00")  # computed total

        response = self.client.get(reverse("master_sheet_csv"))
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertIn("TOTAL claim", body)
        self.assertIn(claim.reference, body)
        self.assertIn("150.00", body)

    def test_standalone_entry_forms(self):
        claim = Claim.objects.create(created_by=self.user)
        for kind in ("photo", "survey", "email", "billing"):
            response = self.client.get(reverse("record_add", args=[kind]))
            self.assertEqual(response.status_code, 200, kind)

        response = self.client.post(
            reverse("record_add", args=["survey"]),
            {"claim": claim.pk, "surveyor_name": "P. Attard", "status": "requested"},
        )
        self.assertRedirects(response, reverse("claim_tab", args=[claim.pk, "surveys"]))
        self.assertEqual(claim.surveys.count(), 1)

        response = self.client.post(
            reverse("record_add", args=["billing"]),
            {
                "claim": claim.pk,
                "description": "Towing",
                "category": "towing",
                "amount": "120.00",
                "status": "pending",
            },
        )
        self.assertRedirects(response, reverse("claim_tab", args=[claim.pk, "billing"]))
        self.assertEqual(claim.billing_items.count(), 1)

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


from django.core import mail  # noqa: E402
from django.test import override_settings  # noqa: E402


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class EmailSendTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="pw")
        self.client.force_login(self.user)

    def test_send_email_delivers_and_logs(self):
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, third_party_insurer="Mapfre",
            insurer_email="claims@mapfre.com.mt", created_by=self.user,
        )
        r = self.client.post(
            reverse("email_send", args=[claim.pk]),
            {"to": "claims@mapfre.com.mt", "subject": "Reminder",
             "body": "Please settle.", "attach": ""},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["claims@mapfre.com.mt"])
        log = claim.emails.get()
        self.assertEqual(log.direction, "out")
        self.assertEqual(log.to_address, "claims@mapfre.com.mt")
        self.assertEqual(log.logged_by, self.user)

    def test_send_email_with_pdf_attachment(self):
        from decimal import Decimal

        from .models import Company

        co = Company.objects.create(
            name="eCabs Ltd", address="St Julians, Malta",
            email="motorclaims@ecabs.com.mt", logo_static="img/companies/ecabs.png",
        )
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="BLY438",
            tp_claim_number="M24109061", labour_amount=Decimal("200.00"),
            created_by=self.user,
        )
        r = self.client.post(
            reverse("email_send", args=[claim.pk]),
            {"company": co.pk, "to": "claims@mapfre.com.mt",
             "subject": "Repairs receipt", "body": "See attached.",
             "attach": "repairs"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.from_email, "motorclaims@ecabs.com.mt")
        self.assertEqual(len(msg.attachments), 1)
        name, content, mimetype = msg.attachments[0]
        self.assertTrue(name.endswith(".pdf"))
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content[:5] == b"%PDF-")
        self.assertEqual(claim.emails.count(), 1)

    def test_attachment_requires_company(self):
        claim = Claim.objects.create(status=Claim.Status.OPEN, created_by=self.user)
        r = self.client.post(
            reverse("email_send", args=[claim.pk]),
            {"to": "x@y.com", "subject": "Hi", "body": "Body", "attach": "statement"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Choose a company to attach a document.")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(claim.emails.count(), 0)

    def test_default_chase_message(self):
        from decimal import Decimal

        from .services.email import default_chase_message

        claim = Claim.objects.create(
            status=Claim.Status.OPEN, third_party_insurer="Elmo",
            parts_amount=Decimal("500.00"), created_by=self.user,
        )
        subject, body = default_chase_message(claim)
        self.assertIn("Elmo", body)
        self.assertIn("500.00", body)
        self.assertTrue(subject.startswith("Payment reminder"))


class PasswordAndBackupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="oldpw12345")

    def test_change_password_requires_login(self):
        r = self.client.get(reverse("password_change"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r.url)

    def test_change_password_works(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("password_change"))
        self.assertEqual(r.status_code, 200)
        r = self.client.post(reverse("password_change"), {
            "old_password": "oldpw12345",
            "new_password1": "brandNew99xy",
            "new_password2": "brandNew99xy",
        })
        self.assertRedirects(r, reverse("password_change_done"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brandNew99xy"))

    def test_password_reset_page_loads(self):
        r = self.client.get(reverse("password_reset"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Reset password")

    def test_password_reset_never_500s_without_email(self):
        # Submitting still redirects to the neutral done page even if sending
        # is not possible (SafePasswordResetView swallows send failures).
        r = self.client.post(reverse("password_reset"), {"email": "staff@example.com"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.url, reverse("password_reset_done"))

    def test_backup_db_command(self):
        import os
        import tempfile
        from pathlib import Path

        from django.conf import settings
        from django.core.management import call_command
        from django.test import override_settings

        with tempfile.TemporaryDirectory() as tmp:
            dbfile = Path(tmp) / "db.sqlite3"
            dbfile.write_bytes(b"SQLite format 3\x00sample")
            dbs = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(dbfile)}}
            with override_settings(DATABASES=dbs, BASE_DIR=Path(tmp)):
                call_command("backup_db")
            made = list((Path(tmp) / "backups").glob("db-*.sqlite3"))
            self.assertEqual(len(made), 1)
            self.assertEqual(made[0].read_bytes(), dbfile.read_bytes())


class AuditTrailTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("auditor", password="pw")
        self.client.force_login(self.user)

    def test_audit_trail_shows_changes(self):
        from decimal import Decimal

        # A create and an update should both appear.
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, vehicle_registration="AUD-1", created_by=self.user,
        )
        claim.parts_amount = Decimal("100.00")
        claim.save()
        Vehicle.objects.create(registration="AUD-VEH", make_model="Test")

        r = self.client.get(reverse("audit_trail"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Audit trail")
        self.assertContains(r, claim.reference)   # claim listed
        self.assertContains(r, "AUD-VEH")          # vehicle listed
        self.assertContains(r, "created")
        self.assertContains(r, "updated")
        self.assertContains(r, "parts_amount")     # changed field named

    def test_audit_trail_filter_by_kind(self):
        Claim.objects.create(status=Claim.Status.OPEN, vehicle_registration="AUD-2", created_by=self.user)
        Vehicle.objects.create(registration="ONLYVEH", make_model="Test")
        r = self.client.get(reverse("audit_trail"), {"kind": "vehicle"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ONLYVEH")
        self.assertNotContains(r, "AUD-2")

    def test_audit_trail_requires_login(self):
        self.client.logout()
        r = self.client.get(reverse("audit_trail"))
        self.assertEqual(r.status_code, 302)


class ReminderPopupTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="pw")
        self.client.force_login(self.user)

    def test_popup_shows_when_reminder_due(self):
        Reminder.objects.create(
            title="Chase Elmo payment",
            due_at=timezone.now() - timedelta(hours=1),  # overdue
        )
        r = self.client.get(reverse("home"))
        self.assertContains(r, "Reminders due")
        self.assertContains(r, "Chase Elmo payment")

    def test_no_popup_when_nothing_due(self):
        # A future reminder is not "due" yet.
        Reminder.objects.create(title="Later", due_at=timezone.now() + timedelta(days=3))
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, "Reminders due")

    def test_completed_reminder_no_popup(self):
        Reminder.objects.create(
            title="Done one", due_at=timezone.now() - timedelta(hours=2),
            completed_at=timezone.now(),
        )
        r = self.client.get(reverse("home"))
        self.assertNotContains(r, "Reminders due")


class InvoiceDateAndNoteTests(TestCase):
    def _company(self, name):
        from .models import Company
        return Company.objects.create(name=name, address="Malta", logo_static="img/companies/vai.png")

    def test_invoice_date_blank_without_bill_date(self):
        from decimal import Decimal

        from .services.invoice_pdf import build_invoice_pdf
        co = self._company("Vai Drive Co Ltd.")
        claim = Claim.objects.create(status=Claim.Status.OPEN, parts_amount=Decimal("100.00"))
        # No bills_sent_on -> the PDF must not stamp today's date.
        import datetime, fitz, tempfile, os
        pdf = build_invoice_pdf(claim, co)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf); path = f.name
        text = fitz.open(path).load_page(0).get_text().replace("\n", " ")
        os.unlink(path)
        self.assertIn("Invoice date: —", text)
        self.assertNotIn(datetime.date.today().strftime("%d/%m/%Y"), text)

    def test_30pct_note_ecabs_only(self):
        from .services.invoice_pdf import build_lou_pdf
        import fitz, tempfile, os
        vai = self._company("Vai Drive Co Ltd.")
        ecabs = self._company("eCabs Ltd")
        from decimal import Decimal
        claim = Claim.objects.create(status=Claim.Status.OPEN, loe_days=8, loe_daily_rate=Decimal("50.00"))

        def note_present(company):
            pdf = build_lou_pdf(claim, company)
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf); path = f.name
            text = fitz.open(path).load_page(0).get_text()
            os.unlink(path)
            return "30% running expenses" in text

        self.assertTrue(note_present(ecabs))
        self.assertFalse(note_present(vai))


class OverviewPageTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="pw")
        self.client.force_login(self.user)

    def test_overview_loads_with_companies(self):
        from .models import Company
        Company.objects.create(name="eCabs Ltd", logo_static="img/companies/ecabs.png")
        r = self.client.get(reverse("overview"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "The claims ecosystem")
        self.assertContains(r, "The claim lifecycle")
        self.assertContains(r, "eCabs Ltd")

    def test_overview_requires_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("overview")).status_code, 302)


class ClaimsAtFaultTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="pw")
        self.client.force_login(self.user)

    def test_only_at_fault_claims_listed(self):
        ours = Claim.objects.create(status=Claim.Status.OPEN, fault=Claim.Fault.OUR_DRIVER,
                                    vehicle_registration="OI-1", created_by=self.user)
        tp = Claim.objects.create(status=Claim.Status.OPEN, fault=Claim.Fault.THIRD_PARTY,
                                  vehicle_registration="TP-9", created_by=self.user)
        r = self.client.get(reverse("claims_at_fault"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "OI-1")
        self.assertNotContains(r, "TP-9")

    def test_inline_save_and_close(self):
        from decimal import Decimal
        c = Claim.objects.create(status=Claim.Status.OPEN, fault=Claim.Fault.OUR_DRIVER,
                                 created_by=self.user)
        # inline save of details / awaiting / estimate
        self.client.post(reverse("at_fault_update", args=[c.pk]),
                         {"details": "Rear-ended", "awaiting_from": "Mario",
                          "estimate_amount": "1250.50"})
        c.refresh_from_db()
        self.assertEqual(c.details, "Rear-ended")
        self.assertEqual(c.awaiting_from, "Mario")
        self.assertEqual(c.estimate_amount, Decimal("1250.50"))
        # close by decision
        self.client.post(reverse("at_fault_update", args=[c.pk]), {"action": "close"})
        c.refresh_from_db()
        self.assertEqual(c.status, Claim.Status.CLOSED)

    def test_at_fault_not_auto_closed_at_zero(self):
        # An at-fault claim with no outstanding must NOT auto-close.
        c = Claim.objects.create(status=Claim.Status.OPEN, fault=Claim.Fault.OUR_DRIVER,
                                 created_by=self.user)
        c.run_workflow()
        c.refresh_from_db()
        self.assertEqual(c.status, Claim.Status.OPEN)


class RepairTypeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("staff", password="pw")
        self.client.force_login(self.user)

    def test_add_type_then_itemise_lines_with_vat(self):
        from decimal import Decimal

        from .models import Company, RepairLine, RepairType

        co = Company.objects.create(name="eCabs Ltd", address="Malta",
                                    logo_static="img/companies/ecabs.png")
        claim = Claim.objects.create(status=Claim.Status.OPEN, created_by=self.user)

        # add a new type — maintained list, not automated, no auto-select
        self.client.post(reverse("repair_type_add", args=[claim.pk]),
                         {"name": "Windscreen replacement"})
        rt = RepairType.objects.get(name="Windscreen replacement")
        self.assertFalse(claim.repair_lines.exists())

        body = RepairType.objects.get(name="Bodywork")

        # tick two types with per-line net costs
        self.client.post(reverse("claim_set_repair_lines", args=[claim.pk]), {
            f"line_{rt.pk}": "on", f"cost_{rt.pk}": "150.00",
            f"line_{body.pk}": "on", f"cost_{body.pk}": "50.00",
        })
        claim.refresh_from_db()
        self.assertEqual(claim.repair_lines.count(), 2)
        # net total is the sum of the ticked lines
        self.assertEqual(claim.repair_lines_total, Decimal("200.00"))
        self.assertEqual(claim.repairs_receipt_net, Decimal("200.00"))

        # unticking removes a line, keeps the other
        self.client.post(reverse("claim_set_repair_lines", args=[claim.pk]), {
            f"line_{rt.pk}": "on", f"cost_{rt.pk}": "150.00",
        })
        claim.refresh_from_db()
        self.assertEqual(claim.repair_lines.count(), 1)
        self.assertEqual(claim.repairs_receipt_net, Decimal("150.00"))

        # receipt PDF renders the itemised lines
        r = self.client.get(reverse("claim_repairs_pdf", args=[claim.pk]), {"company": co.pk})
        self.assertEqual(r["Content-Type"], "application/pdf")

    def test_starter_types_seeded(self):
        from .models import RepairType
        self.assertTrue(RepairType.objects.filter(name="Bodywork").exists())

    def test_repair_line_selector_on_documents_page(self):
        claim = Claim.objects.create(status=Claim.Status.OPEN, created_by=self.user)
        r = self.client.get(reverse("claim_documents", args=[claim.pk]), {"doc": "repairs"})
        self.assertContains(r, "Types of repair &amp; cost")
        self.assertContains(r, "Add a new repair type")


class GarageJobTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("garage", password="pw")
        self.client.force_login(self.user)

    def test_seeded_rows_show_on_page(self):
        r = self.client.get(reverse("garage_jobs"))
        self.assertContains(r, "ACL Garage")
        self.assertContains(r, "Mohammed Asif Khaled")

    def test_add_edit_close_reopen_delete_row(self):
        from datetime import date

        from .models import GarageJob

        before = GarageJob.objects.count()
        self.client.post(reverse("garage_job_add"))
        job = GarageJob.objects.order_by("-id").first()
        self.assertEqual(GarageJob.objects.count(), before + 1)

        # inline edit
        self.client.post(reverse("garage_job_update", args=[job.pk]), {
            "accident_date": "2026-06-01", "survey_date": "2026-06-05",
            "plate_no": "ZZZ999", "client": "Test Client", "make": "Toyota",
            "claim_no": "CL123", "surveyor": "Jo", "insurance": "Elmo",
            "report_received": "2026-06-10", "go_ahead": "Yes, go",
        })
        job.refresh_from_db()
        self.assertEqual(job.plate_no, "ZZZ999")
        self.assertEqual(job.accident_date, date(2026, 6, 1))
        self.assertTrue(job.go_ahead_yes)

        # close then reopen
        self.client.post(reverse("garage_job_update", args=[job.pk]), {"action": "close"})
        job.refresh_from_db(); self.assertTrue(job.is_closed)
        self.client.post(reverse("garage_job_update", args=[job.pk]), {"action": "reopen"})
        job.refresh_from_db(); self.assertFalse(job.is_closed)

        # delete
        self.client.post(reverse("garage_job_update", args=[job.pk]), {"action": "delete"})
        self.assertFalse(GarageJob.objects.filter(pk=job.pk).exists())


class GarageAccessTests(TestCase):
    """A garage-only user (Mario) is confined to the ACL Garage page; an admin
    (Francis) keeps full access and sees the same rows."""

    def setUp(self):
        from django.contrib.auth.models import Group

        User = get_user_model()
        self.mario = User.objects.create_user("mario", password="pw")
        group, _ = Group.objects.get_or_create(name="Garage")
        self.mario.groups.add(group)
        self.francis = User.objects.create_superuser("francis", password="pw")

    def test_make_garage_user_command(self):
        from django.core.management import call_command

        from .roles import is_garage_user
        call_command("make_garage_user", "luigi", password="pw", full_name="Luigi Verde")
        u = get_user_model().objects.get(username="luigi")
        self.assertTrue(is_garage_user(u))
        self.assertFalse(u.is_staff)
        self.assertEqual(u.first_name, "Luigi")

    def test_garage_user_confined_to_garage_page(self):
        self.client.force_login(self.mario)
        self.assertEqual(self.client.get(reverse("garage_jobs")).status_code, 200)
        # anything else bounces to the worklist
        for name in ("dashboard", "claim_list", "claims_at_fault", "master_sheet"):
            r = self.client.get(reverse(name))
            self.assertRedirects(r, reverse("garage_jobs"),
                                 fetch_redirect_response=False)

    def test_garage_user_nav_is_trimmed(self):
        self.client.force_login(self.mario)
        html = self.client.get(reverse("garage_jobs")).content
        self.assertIn(b"ACL Garage", html)
        self.assertNotIn(b"Dashboard", html)
        self.assertNotIn(b"Master sheet", html)

    def test_admin_has_full_access_and_sees_garage(self):
        self.client.force_login(self.francis)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)
        html = self.client.get(reverse("garage_jobs")).content
        self.assertIn(b"Dashboard", html)  # full nav
        self.assertIn(b"ACL Garage", html)

    def test_edits_are_attributed_and_shared(self):
        from .models import GarageJob

        self.client.force_login(self.mario)
        self.client.post(reverse("garage_job_add"))
        job = GarageJob.objects.order_by("-id").first()
        self.client.post(reverse("garage_job_update", args=[job.pk]),
                         {"plate_no": "MAR123", "total": "1250.50", "go_ahead": ""})
        job.refresh_from_db()
        self.assertEqual(job.updated_by, self.mario)
        self.assertEqual(str(job.total), "1250.50")
        # Francis sees Mario's row and its total on the shared page
        self.client.force_login(self.francis)
        html = self.client.get(reverse("garage_jobs")).content
        self.assertIn(b"MAR123", html)
        self.assertIn(b"1250.50", html)


class ImportAccidentsTests(TestCase):
    def _make_xlsx(self, path):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Date of Acc", "Our Reg", "Driver Name", "TP Reg", "Vehicle Make",
                   "TP Insurance", "Report Type", "Fault (OI / TP)"])
        import datetime
        ws.append([datetime.datetime(2021, 1, 1), "CLY142", "Isaac Zammit", "BBX937",
                   "Nissan Qashqai", "MSI", "F 2 R", "TP"])
        ws.append([datetime.datetime(2021, 2, 3), "FLY098", "Sam", "JBH412",
                   "Peugeot 206", "MSI", "Etars - 307250", "OI"])
        ws.append([None, "", "", "", "", "", "", ""])  # empty -> skipped
        wb.save(path)

    def test_import_maps_and_creates(self):
        import tempfile
        from django.core.management import call_command
        from .models import Claim

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        self._make_xlsx(path)
        call_command("import_accidents", path, status="closed")

        self.assertEqual(Claim.objects.count(), 2)  # empty row skipped
        a = Claim.objects.get(vehicle_registration="CLY142")
        self.assertEqual(a.driver_name, "Isaac Zammit")
        self.assertEqual(a.third_party_registration, "BBX937")
        self.assertEqual(a.third_party_insurer, "MSI")
        self.assertEqual(a.fault, Claim.Fault.THIRD_PARTY)
        self.assertEqual(a.report_type, Claim.ReportType.F2R)
        self.assertEqual(a.status, Claim.Status.CLOSED)
        b = Claim.objects.get(vehicle_registration="FLY098")
        self.assertEqual(b.fault, Claim.Fault.OUR_DRIVER)          # OI
        self.assertEqual(b.report_type, Claim.ReportType.ETARS)
        self.assertEqual(b.police_report_number, "Etars - 307250")  # original kept

    def test_dry_run_saves_nothing(self):
        import tempfile
        from django.core.management import call_command
        from .models import Claim

        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        self._make_xlsx(path)
        call_command("import_accidents", path, "--dry-run")
        self.assertEqual(Claim.objects.count(), 0)
