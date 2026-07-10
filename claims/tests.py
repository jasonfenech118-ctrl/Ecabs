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
        claim.driver_name = "Test Driver"
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
            {"driver_name": "Maria Vella", "fault": "unknown"},
        )
        self.assertEqual(response.status_code, 200)
        claim.refresh_from_db()
        self.assertEqual(claim.driver_name, "Maria Vella")
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

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
