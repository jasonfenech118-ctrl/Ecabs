"""
Core data model for the insurance claim system.

Claim is the central record; photos, surveys, emails, reminders and billing
items all hang off it. Files live in Google Drive — only the Drive file ID
and metadata are stored here.
"""

from datetime import timedelta
from decimal import Decimal

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

    # Renewal due date — drives the reminders on the dashboard.
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
        OUR_DRIVER = "our_driver", "Our insured (OI) at fault"
        THIRD_PARTY = "third_party", "Third party (TP) at fault"
        SHARED = "shared", "Shared / knock for knock"

    class ReportType(models.TextChoices):
        ETARS = "etars", "ETARS"
        F2R = "f2r", "F2R (front to rear)"
        RAR = "rar", "RAR (police report)"
        OTHER = "other", "Other"

    reference = models.CharField(max_length=20, unique=True, editable=False)
    # Internal company case number — a running count that never resets, assigned
    # once a draft becomes a real claim. Shown as "VD-00001".
    case_number = models.PositiveIntegerField(
        unique=True, null=True, blank=True, editable=False
    )
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
    report_type = models.CharField(
        max_length=10, choices=ReportType.choices, blank=True
    )
    police_report_number = models.CharField(
        "Report reference no", max_length=50, blank=True
    )
    fault = models.CharField(
        "Fault (OI / TP)", max_length=20, choices=Fault.choices, default=Fault.UNKNOWN
    )

    # Third party
    third_party_registration = models.CharField("TP reg no", max_length=20, blank=True)
    third_party_vehicle = models.CharField("TP vehicle make", max_length=100, blank=True)
    third_party_name = models.CharField("TP driver name", max_length=100, blank=True)
    third_party_phone = models.CharField("TP driver contact no", max_length=30, blank=True)
    third_party_owner_name = models.CharField("TP owner name", max_length=100, blank=True)
    third_party_owner_phone = models.CharField(
        "TP owner contact no", max_length=30, blank=True
    )
    third_party_insurer = models.CharField("TP insurance", max_length=100, blank=True)
    tp_claim_number = models.CharField("TP claim no", max_length=50, blank=True)

    # Second third party (multi-vehicle accidents)
    tp2_registration = models.CharField("TP2 reg no", max_length=20, blank=True)
    tp2_owner_name = models.CharField("TP2 owner name", max_length=100, blank=True)
    tp2_owner_phone = models.CharField("TP2 contact no", max_length=30, blank=True)
    tp2_insurer = models.CharField("TP2 insurance", max_length=100, blank=True)

    # Insurance
    insurer = models.CharField("Our insurer", max_length=100, blank=True)
    policy_number = models.CharField(max_length=50, blank=True)
    insurer_claim_number = models.CharField(
        "Our insurer claim no", max_length=50, blank=True
    )
    excess_amount = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True
    )

    # Assessment
    drivable = models.BooleanField("Drivable?", null=True, blank=True)
    survey_in_hand = models.BooleanField("Survey in hand?", default=False)
    estimate_amount = models.DecimalField(
        "Estimate claim amt", max_digits=10, decimal_places=2, null=True, blank=True
    )

    # Recovery financials (net amounts being recovered from the third party)
    bills_sent_on = models.DateField("Bills sent", null=True, blank=True)
    invoice_number = models.CharField("Invoice no", max_length=50, blank=True)
    labour_amount = models.DecimalField(
        "Labour (net)", max_digits=10, decimal_places=2, null=True, blank=True
    )
    spray_material_amount = models.DecimalField(
        "Spray + material (net)", max_digits=10, decimal_places=2, null=True, blank=True
    )
    parts_amount = models.DecimalField(
        "Parts", max_digits=10, decimal_places=2, null=True, blank=True
    )
    loe_days = models.PositiveIntegerField("LOE days", null=True, blank=True)
    loe_daily_rate = models.DecimalField(
        "LOE daily amount", max_digits=10, decimal_places=2, null=True, blank=True
    )
    others_amount = models.DecimalField(
        "Others", max_digits=10, decimal_places=2, null=True, blank=True
    )
    settlement_amount = models.DecimalField(
        "Settlement", max_digits=10, decimal_places=2, null=True, blank=True
    )
    amount_paid = models.DecimalField(
        "Amount paid", max_digits=10, decimal_places=2, null=True, blank=True
    )
    offset_amount = models.DecimalField(
        "Offset", max_digits=10, decimal_places=2, null=True, blank=True
    )

    # Workflow / chasing
    next_action = models.CharField(
        "Claim phase / next action", max_length=200, blank=True
    )
    chase_on = models.DateField("Chase on", null=True, blank=True)
    urgent = models.BooleanField(default=False)

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
        # Assign the company case number once the claim is real (off draft).
        if self.case_number is None and self.status != self.Status.DRAFT:
            self.case_number = self._next_case_number()
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

    @classmethod
    def _next_case_number(cls):
        return (cls.objects.aggregate(m=models.Max("case_number"))["m"] or 0) + 1

    @property
    def case_ref(self):
        """Company case number, e.g. VD-00042 (blank until off draft)."""
        return f"VD-{self.case_number:05d}" if self.case_number else ""

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

    # --- Recovery arithmetic (mirrors the Excel tracker's columns) ---------

    @property
    def loss_of_earnings(self):
        if self.loe_days and self.loe_daily_rate:
            return self.loe_days * self.loe_daily_rate
        return Decimal("0")

    @property
    def total_claim_amount(self):
        """Everything being recovered from the third party."""
        parts = [
            self.labour_amount,
            self.spray_material_amount,
            self.parts_amount,
            self.loss_of_earnings,
            self.others_amount,
        ]
        return sum((p or Decimal("0")) for p in parts)

    @property
    def outstanding_amount(self):
        """Still owed: total claim minus what was paid and offset."""
        return (
            self.total_claim_amount
            - (self.amount_paid or Decimal("0"))
            - (self.offset_amount or Decimal("0"))
        )


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
    # Optional: reminders can stand alone or be linked to a claim.
    claim = models.ForeignKey(
        Claim,
        on_delete=models.CASCADE,
        related_name="reminders",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=200)
    notes = models.TextField(blank=True)
    due_at = models.DateTimeField(db_index=True)
    # Set for reminders the system generates (insurance renewals, chase
    # dates). auto_key identifies the source so syncing stays idempotent.
    is_auto = models.BooleanField(default=False)
    auto_key = models.CharField(max_length=100, blank=True, default="", db_index=True)
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
