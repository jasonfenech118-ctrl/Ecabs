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
        # No company chosen yet -> prompt, no statement.
        r = self.client.get(reverse("claim_invoice", args=[claim.pk]))
        self.assertContains(r, "select company")
        self.assertNotContains(r, "Total NET")
        # Company chosen -> full statement with letterhead + line items + total.
        r = self.client.get(reverse("claim_invoice", args=[claim.pk]), {"company": co.pk})
        self.assertContains(r, "Vai Drive Co Ltd.")
        self.assertContains(r, "Banif Bank")
        self.assertContains(r, "Labour")
        self.assertContains(r, "Loss of earnings")
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
        r = self.client.get(reverse("claim_repairs", args=[claim.pk]), {"company": co.pk})
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
        # Selector page
        r = self.client.get(reverse("claim_lou", args=[claim.pk]), {"company": co.pk})
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

    def test_invoice_date_is_bill_date(self):
        from datetime import date
        from decimal import Decimal

        from .models import Company

        co = Company.objects.create(
            name="Vai Drive Co Ltd.", address="Malta", logo_static="img/companies/vai.png"
        )
        claim = Claim.objects.create(
            status=Claim.Status.OPEN, bills_sent_on=date(2026, 5, 20),
            parts_amount=Decimal("100.00"), created_by=self.user,
        )
        r = self.client.get(reverse("claim_invoice", args=[claim.pk]), {"company": co.pk})
        self.assertContains(r, "20/05/2026")
        self.assertContains(r, "Invoice date")

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
