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


class Company(models.Model):
    """A group company whose letterhead an invoice can be issued under.
    Chosen at the point of finalising the invoice, not stored on the claim."""

    name = models.CharField(max_length=120)
    address = models.TextField(help_text="Full postal address, one line each")
    phone = models.CharField(max_length=40, blank=True)
    email = models.CharField(max_length=120, blank=True)
    website = models.CharField(max_length=120, blank=True)
    vat_no = models.CharField("VAT no", max_length=40, blank=True)
    exo_number = models.CharField("EXO number", max_length=40, blank=True)
    # Payment details shown on the invoice.
    bank_name = models.CharField(max_length=80, blank=True)
    iban = models.CharField(max_length=60, blank=True)
    account_no = models.CharField(max_length=40, blank=True)
    swift = models.CharField(max_length=20, blank=True)
    # Static path (under static/img/companies/) or an uploaded logo.
    logo_static = models.CharField(
        max_length=120, blank=True,
        help_text="Path under static/, e.g. img/companies/vai.png",
    )
    logo = models.ImageField(upload_to="company_logos/", blank=True)
    footer_note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


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

    class Liability(models.TextChoices):
        UNKNOWN = "unknown", "Not yet decided"
        ACCEPTED = "accepted", "Accepted (Yes)"
        DISPUTED = "disputed", "Disputed (No)"

    reference = models.CharField(max_length=20, unique=True, editable=False)
    # Internal company case number — a running count that never resets, assigned
    # once a draft becomes a real claim. Shown as "VD-00001".
    case_number = models.PositiveIntegerField(
        unique=True, null=True, blank=True, editable=False
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    # Vehicle
    vehicle_registration = models.CharField(max_length=20, blank=True)
    vehicle_make_model = models.CharField(max_length=100, blank=True)
    driver_name = models.CharField("Our driver name", max_length=100, blank=True)

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
    third_party_registration = models.CharField("TP number plate", max_length=20, blank=True)
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
    tp2_registration = models.CharField("TP2 number plate", max_length=20, blank=True)
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

    # Assessment & workflow
    drivable = models.BooleanField("Drivable?", null=True, blank=True)
    survey_booked = models.BooleanField("Survey booked?", default=False)
    survey_date = models.DateField(null=True, blank=True)
    survey_in_hand = models.BooleanField("Survey received?", default=False)
    liability = models.CharField(
        "Liability accepted?", max_length=10,
        choices=Liability.choices, default=Liability.UNKNOWN,
    )
    liability_chase_date = models.DateField(
        "Liability chase date", null=True, blank=True,
        help_text="Optional manual date; otherwise chased monthly",
    )
    insurer_contact = models.CharField(
        "Chase — who & contact details", max_length=200, blank=True
    )
    insurer_email = models.EmailField(
        "Insurer email", max_length=254, blank=True,
        help_text="Where chase/recovery emails are sent for this claim",
    )
    estimate_amount = models.DecimalField(
        "Estimate claim amt", max_digits=10, decimal_places=2, null=True, blank=True
    )
    # At-fault register (where our insured is liable): free-text columns.
    details = models.CharField("Details", max_length=255, blank=True)
    awaiting_from = models.CharField("Awaiting from", max_length=255, blank=True)

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
    # Manually chosen from a maintained list — used as the repairs-receipt
    # description instead of the auto-built component list.
    repair_type = models.ForeignKey(
        "RepairType", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="claims", verbose_name="Type of repair",
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

    def run_workflow(self):
        """Auto-advance the claim's status:
        - survey received + liability accepted -> chasing (awaiting insurer)
        - fully paid (outstanding zero) -> closed
        A claim with any outstanding balance stays open/chasing.
        Returns True if anything changed."""
        if self.status == self.Status.DRAFT:
            return False
        # At-fault claims (our insured liable) are not a recovery — they are
        # never auto-advanced or auto-closed; they close only by decision.
        if self.fault == self.Fault.OUR_DRIVER:
            return False
        changed = False
        if (
            self.survey_in_hand
            and self.liability == self.Liability.ACCEPTED
            and self.status in (self.Status.OPEN, self.Status.AWAITING_SURVEY)
        ):
            self.status = self.Status.AWAITING_INSURER
            if not self.chase_on:
                self.chase_on = timezone.localdate()
            changed = True
        if (
            self.total_claim_amount > 0
            and self.outstanding_amount <= 0
            and self.status
            not in (self.Status.DRAFT, self.Status.CLOSED, self.Status.REJECTED)
        ):
            self.status = self.Status.CLOSED
            changed = True
        if changed:
            self.save()
        return changed

    # --- Recovery arithmetic (mirrors the Excel tracker's columns) ---------

    @property
    def loss_of_earnings(self):
        if self.loe_days and self.loe_daily_rate:
            return self.loe_days * self.loe_daily_rate
        return Decimal("0")

    @property
    def other_charges_total(self):
        """Sum of the manual 'other' line items (wheel alignment, windscreen…)."""
        if not self.pk:
            return Decimal("0")
        return sum((oc.amount or Decimal("0") for oc in self.other_charges.all()), Decimal("0"))

    @property
    def total_claim_amount(self):
        """Everything being recovered from the third party."""
        parts = [
            self.labour_amount,
            self.spray_material_amount,
            self.parts_amount,
            self.loss_of_earnings,
        ]
        return sum((p or Decimal("0")) for p in parts) + self.other_charges_total

    @property
    def outstanding_amount(self):
        """Still owed: total claim minus what was paid and offset."""
        return (
            self.total_claim_amount
            - (self.amount_paid or Decimal("0"))
            - (self.offset_amount or Decimal("0"))
        )

    @property
    def repairs_total(self):
        """Repair costs only (labour, spray+material, parts, others) — no LOE.
        Used net-of-VAT on the repairs receipt."""
        parts = [self.labour_amount, self.spray_material_amount, self.parts_amount]
        return sum((p or Decimal("0")) for p in parts) + self.other_charges_total

    @property
    def repair_lines_total(self):
        """Net total of the itemised repair lines (type + cost), ex-VAT."""
        return sum((rl.cost or Decimal("0")) for rl in self.repair_lines.all())

    @property
    def repairs_receipt_net(self):
        """Net for the repairs receipt: the itemised repair lines when any are
        set, otherwise the labour/spray/parts figures."""
        if self.repair_lines.exists():
            return self.repair_lines_total
        return self.repairs_total

    def repairs_label(self):
        """Description shown on the repairs receipt: the manually chosen repair
        type when set, otherwise the auto-built component list."""
        if self.repair_type_id:
            return self.repair_type.name
        return self.repairs_description()

    def repairs_description(self):
        """Comma-joined list of the repair components present on the claim."""
        bits = []
        if self.labour_amount:
            bits.append("Labour")
        if self.spray_material_amount:
            bits.append("Spray, Spray Material")
        if self.parts_amount:
            bits.append("Parts")
        for oc in self.other_charges.all():
            if oc.amount and oc.description:
                bits.append(oc.description)
        return ", ".join(bits) or "Repairs"

    def invoice_lines(self):
        """Line items for the statement/invoice, from the recovery figures.
        Each: (item, qty, rate, amount). Zero lines are omitted."""
        lines = []
        if self.labour_amount:
            lines.append(("Labour", 1, self.labour_amount, self.labour_amount))
        if self.spray_material_amount:
            lines.append(
                ("Spray + Material", 1, self.spray_material_amount,
                 self.spray_material_amount)
            )
        if self.parts_amount:
            lines.append(("Parts", 1, self.parts_amount, self.parts_amount))
        if self.loe_days and self.loe_daily_rate:
            lines.append(
                ("Loss of earnings", self.loe_days, self.loe_daily_rate,
                 self.loss_of_earnings)
            )
        for oc in self.other_charges.all():
            if oc.amount:
                lines.append((oc.description or "Other", 1, oc.amount, oc.amount))
        return lines


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


class OtherCharge(models.Model):
    """A manual 'other' recovery line on a claim (wheel alignment, windscreen,
    etc.). A claim can have none, one, or many."""

    claim = models.ForeignKey(
        Claim, on_delete=models.CASCADE, related_name="other_charges"
    )
    description = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.description} — €{self.amount}"


class DailyRate(models.Model):
    """A maintained daily rate for loss of earnings / loss of use, editable by
    staff on the Maintenance page and selectable on the claim form."""

    name = models.CharField(max_length=100)
    amount = models.DecimalField("Daily amount", max_digits=10, decimal_places=2)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return f"{self.name} — €{self.amount}/day"


class RepairType(models.Model):
    """A maintained type of repair, chosen (not auto-derived) for a claim's
    repairs receipt. Staff add their own for future selection."""

    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class RepairLine(models.Model):
    """One itemised repair on a claim's receipt: a chosen repair type and its
    net (ex-VAT) cost. VAT @18% is added automatically on the receipt."""

    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, related_name="repair_lines")
    repair_type = models.ForeignKey(
        RepairType, on_delete=models.PROTECT, related_name="lines"
    )
    cost = models.DecimalField("Cost (net)", max_digits=10, decimal_places=2, default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    @property
    def label(self):
        return self.repair_type.name

    def __str__(self):
        return f"{self.repair_type.name} — €{self.cost}"


class GarageJob(models.Model):
    """A vehicle sent to a panel-beater garage (e.g. ACL Garage) for repair.
    A simple tracking register mirroring the garage's worksheet: survey, when
    the report came back, and whether we have the insurer's go-ahead."""

    class Garage(models.TextChoices):
        ACL = "acl", "ACL Garage"

    garage = models.CharField(
        max_length=20, choices=Garage.choices, default=Garage.ACL, db_index=True
    )
    accident_date = models.DateField("Accident date", null=True, blank=True)
    survey_date = models.DateField("Survey date", null=True, blank=True)
    plate_no = models.CharField("No plate", max_length=20, blank=True)
    client = models.CharField("Client", max_length=200, blank=True)
    make = models.CharField("Make", max_length=120, blank=True)
    claim_no = models.CharField("Claim No", max_length=60, blank=True)
    surveyor = models.CharField("Surveyor", max_length=120, blank=True)
    insurance = models.CharField("Insurance", max_length=120, blank=True)
    # Free text so it can hold a date or a note like "Pending part".
    report_received = models.CharField("Report received", max_length=120, blank=True)
    go_ahead = models.CharField("Go ahead?", max_length=200, blank=True)
    total = models.DecimalField(
        "Total (€)", max_digits=10, decimal_places=2, null=True, blank=True
    )

    is_closed = models.BooleanField(default=False, db_index=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="Last edited by",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-accident_date", "-id"]

    @property
    def go_ahead_yes(self):
        return self.go_ahead.strip().lower().startswith("yes")

    def __str__(self):
        return f"{self.plate_no or '—'} · {self.client or '—'}"


class GarageInvoice(models.Model):
    """An invoice the panel-beater garage raises to bill the hire company for
    repairs. The issuer (garage) details default to ACR Garage but can be
    edited per invoice; totals (subtotal, discount, VAT, balance) compute from
    the lines."""

    # Issuer — the garage raising the invoice.
    issuer_name = models.CharField("Garage name", max_length=120, default="ACR Garage")
    payable_to = models.CharField("Payable to", max_length=120, default="Mario Galea")
    issuer_contact = models.CharField("Contact details", max_length=120, default="9946 8105")
    issuer_vat = models.CharField("VAT No", max_length=60, default="1579 7311 MT")
    issuer_email = models.EmailField("Email", default="acrgarage@gmail.com")

    invoice_no = models.CharField("Invoice No", max_length=40, blank=True)
    # Manual date — never auto-filled (house rule for all documents).
    invoice_date = models.DateField("Invoice date", null=True, blank=True)

    # Bill-to — the customer (hire company).
    bill_to = models.CharField("Bill to", max_length=160, blank=True)
    bill_contact_name = models.CharField("Contact name", max_length=160, blank=True)
    bill_company_name = models.CharField("Client company name", max_length=200, blank=True)
    bill_address = models.TextField("Address", blank=True)
    bill_email = models.CharField("Email", max_length=200, blank=True)
    bill_vat = models.CharField("Client VAT", max_length=60, blank=True)

    discount_pct = models.DecimalField(
        "Discount %", max_digits=5, decimal_places=2, default=0
    )
    vat_rate = models.DecimalField(
        "VAT rate %", max_digits=5, decimal_places=2, default=18
    )
    remarks = models.TextField("Remarks / payment instructions", blank=True)

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        PAID = "paid", "Paid"

    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+", verbose_name="Last edited by",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"{self.invoice_no or 'Invoice'} — {self.bill_to or '—'}"

    def get_absolute_url(self):
        return reverse("garage_invoice_edit", args=[self.pk])

    @property
    def subtotal(self):
        return sum((ln.amount or Decimal("0")) for ln in self.lines.all())

    @property
    def discount_amount(self):
        return (self.subtotal * (self.discount_pct or Decimal("0")) / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def subtotal_less_discount(self):
        return self.subtotal - self.discount_amount

    @property
    def vat_amount(self):
        return (self.subtotal_less_discount * (self.vat_rate or Decimal("0")) / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def balance_due(self):
        return self.subtotal_less_discount + self.vat_amount


class GarageInvoiceLine(models.Model):
    """One repair line on a garage invoice."""

    invoice = models.ForeignKey(
        GarageInvoice, on_delete=models.CASCADE, related_name="lines"
    )
    line_date = models.DateField("Date", null=True, blank=True)
    reg_no = models.CharField("Reg No", max_length=20, blank=True)
    description = models.CharField("Description", max_length=255, blank=True)
    unit_price = models.DecimalField(
        "Unit price", max_digits=10, decimal_places=2, null=True, blank=True
    )
    amount = models.DecimalField("Total", max_digits=10, decimal_places=2, default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.reg_no} — {self.description}"
