"""
Core data model for the insurance claim system.

Claim is the central record; photos, surveys, emails, reminders and billing
items all hang off it. Files live in Google Drive — only the Drive file ID
and metadata are stored here.
"""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from simple_history.models import HistoricalRecords


def compliance_state(due_date, soon_days=30):
    """'overdue', 'soon' (within soon_days), 'ok', or '' when no date is set."""
    if not due_date:
        return ""
    today = timezone.localdate()
    if due_date < today:
        return "overdue"
    if due_date <= today + timedelta(days=soon_days):
        return "soon"
    return "ok"


class Vehicle(models.Model):
    """A fleet vehicle. Retire (close) vehicles that leave the company rather
    than deleting them, so their claim history keeps its context."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RETIRED = "retired", "No longer in fleet"

    registration = models.CharField(max_length=20, unique=True)
    make_model = models.CharField(max_length=100, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    acquired_on = models.DateField(null=True, blank=True)
    retired_on = models.DateField(null=True, blank=True)

    # Compliance due dates — drive the renewal reminders.
    vrt_due = models.DateField("VRT due", null=True, blank=True)
    licence_due = models.DateField("Road licence due", null=True, blank=True)
    insurance_due = models.DateField("Insurance renewal", null=True, blank=True)

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["registration"]

    def __str__(self):
        return self.registration

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE

    def compliance_items(self):
        """(label, due date, state) for each tracked renewal."""
        return [
            ("VRT", self.vrt_due, compliance_state(self.vrt_due)),
            ("Road licence", self.licence_due, compliance_state(self.licence_due)),
            ("Insurance", self.insurance_due, compliance_state(self.insurance_due)),
        ]

    @property
    def worst_compliance_state(self):
        states = [s for _, _, s in self.compliance_items() if s]
        if "overdue" in states:
            return "overdue"
        if "soon" in states:
            return "soon"
        return "ok" if states else ""

    def retire(self):
        self.status = self.Status.RETIRED
        self.retired_on = timezone.localdate()
        self.save()

    def reactivate(self):
        self.status = self.Status.ACTIVE
        self.retired_on = None
        self.save()


class Claim(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        AWAITING_SURVEY = "awaiting_survey", "Awaiting survey"
        AWAITING_INSURER = "awaiting_insurer", "Awaiting insurer"
        SETTLED = "settled", "Settled"
        CLOSED = "closed", "Closed"
        REJECTED = "rejected", "Rejected"

    class Fault(models.TextChoices):
        UNKNOWN = "unknown", "Not yet determined"
        OUR_DRIVER = "our_driver", "Our driver at fault"
        THIRD_PARTY = "third_party", "Third party at fault"
        SHARED = "shared", "Shared / knock for knock"

    reference = models.CharField(max_length=20, unique=True, editable=False)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    # Vehicle & driver
    vehicle_registration = models.CharField(max_length=20, blank=True)
    vehicle_make_model = models.CharField(max_length=100, blank=True)
    driver_name = models.CharField(max_length=100, blank=True)
    driver_phone = models.CharField(max_length=30, blank=True)
    driver_email = models.EmailField(blank=True)

    # Accident details
    accident_date = models.DateField(null=True, blank=True)
    accident_time = models.TimeField(null=True, blank=True)
    accident_location = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    police_report_number = models.CharField(max_length=50, blank=True)
    fault = models.CharField(max_length=20, choices=Fault.choices, default=Fault.UNKNOWN)

    # Third party
    third_party_name = models.CharField(max_length=100, blank=True)
    third_party_phone = models.CharField(max_length=30, blank=True)
    third_party_vehicle = models.CharField(max_length=100, blank=True)
    third_party_insurer = models.CharField(max_length=100, blank=True)

    # Insurance
    insurer = models.CharField(max_length=100, blank=True)
    policy_number = models.CharField(max_length=50, blank=True)
    excess_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    # Google Drive folder for this claim (one folder per claim)
    drive_folder_id = models.CharField(max_length=100, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="claims_created",
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} — {self.vehicle_registration or 'no vehicle'}"

    def get_absolute_url(self):
        return reverse("claim_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = self._next_reference()
        super().save(*args, **kwargs)

    @classmethod
    def _next_reference(cls):
        year = timezone.now().year
        prefix = f"CLM-{year}-"
        last = (
            cls.objects.filter(reference__startswith=prefix)
            .order_by("-reference")
            .values_list("reference", flat=True)
            .first()
        )
        seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
        return f"{prefix}{seq:04d}"

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT

    @property
    def is_open(self):
        return self.status not in (
            self.Status.SETTLED,
            self.Status.CLOSED,
            self.Status.REJECTED,
        )

    def submit(self):
        """Promote a draft to an open claim."""
        if self.is_draft:
            self.status = self.Status.OPEN
            self.submitted_at = timezone.now()
            self.save()


class AccidentPhoto(models.Model):
    """A photo stored in Google Drive; only ID + metadata live here."""

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="photos")
    drive_file_id = models.CharField(max_length=100, blank=True)
    file_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    caption = models.CharField(max_length=255, blank=True)
    web_view_link = models.URLField(blank=True)
    # Set when Drive is not configured and the file was stored locally instead.
    local_file = models.FileField(upload_to="photos/%Y/%m/", blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.file_name


class Survey(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="surveys")
    surveyor_name = models.CharField(max_length=100)
    surveyor_company = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.REQUESTED
    )
    scheduled_for = models.DateTimeField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)
    report_drive_file_id = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Survey by {self.surveyor_name} ({self.get_status_display()})"


class EmailLog(models.Model):
    class Direction(models.TextChoices):
        INBOUND = "in", "Received"
        OUTBOUND = "out", "Sent"

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="emails")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    from_address = models.CharField(max_length=255)
    to_address = models.CharField(max_length=255)
    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    sent_at = models.DateTimeField(default=timezone.now)
    # Populated when the message was pulled in via the Gmail API.
    gmail_message_id = models.CharField(max_length=100, blank=True)
    logged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"[{self.get_direction_display()}] {self.subject}"


class Reminder(models.Model):
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="reminders")
    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    due_at = models.DateTimeField(db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reminders",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["completed_at", "due_at"]

    def __str__(self):
        return self.title

    @property
    def is_done(self):
        return self.completed_at is not None

    @property
    def is_overdue(self):
        return not self.is_done and self.due_at < timezone.now()


class BillingItem(models.Model):
    class Category(models.TextChoices):
        REPAIR = "repair", "Repair"
        TOWING = "towing", "Towing"
        SURVEY = "survey", "Survey fee"
        EXCESS = "excess", "Excess"
        LEGAL = "legal", "Legal"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        INVOICED = "invoiced", "Invoiced"
        PAID = "paid", "Paid"
        WRITTEN_OFF = "written_off", "Written off"

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="billing_items")
    description = models.CharField(max_length=255)
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.OTHER
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    invoice_number = models.CharField(max_length=50, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.description} — €{self.amount}"
