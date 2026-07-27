from django import forms

from .models import BillingItem, Claim, Company, EmailLog, Reminder, Survey, Vehicle


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            "registration",
            "make_model",
            "owner",
            "year",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class ClaimForm(forms.ModelForm):
    """Intake form. Every field is optional while the claim is a draft —
    auto-save must never be blocked by validation."""

    class Meta:
        model = Claim
        fields = [
            "vehicle_registration",
            "vehicle_make_model",
            "driver_name",
            "accident_date",
            "accident_time",
            "accident_location",
            "description",
            "report_type",
            "police_report_number",
            "fault",
            "third_party_registration",
            "third_party_vehicle",
            "third_party_name",
            "third_party_phone",
            "third_party_owner_name",
            "third_party_owner_phone",
            "third_party_insurer",
            "tp_claim_number",
            "tp2_registration",
            "tp2_owner_name",
            "tp2_owner_phone",
            "tp2_insurer",
            "insurer",
            "policy_number",
            "insurer_claim_number",
            "insurer_contact",
            "insurer_email",
            "excess_amount",
            "details",
            "awaiting_from",
            "drivable",
            "survey_booked",
            "survey_date",
            "survey_in_hand",
            "liability",
            "liability_chase_date",
            "estimate_amount",
            "bills_sent_on",
            "invoice_number",
            "labour_amount",
            "spray_material_amount",
            "parts_amount",
            "loe_days",
            "loe_daily_rate",
            "settlement_amount",
            "amount_paid",
            "offset_amount",
            "next_action",
            "chase_on",
            "urgent",
        ]
        widgets = {
            # A native <input type="date"> only shows its value when it is in
            # ISO YYYY-MM-DD form, so pin the render format — otherwise the saved
            # date renders in a localized format the browser can't read and the
            # field appears blank ("coming and going").
            "accident_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "accident_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "bills_sent_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "chase_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "survey_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "liability_chase_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 5}),
            "drivable": forms.NullBooleanSelect(),
            # Suggests active fleet vehicles via the datalist in claim_form.html.
            "vehicle_registration": forms.TextInput(attrs={"list": "vehicle-regs"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False


class ClaimStatusForm(forms.ModelForm):
    class Meta:
        model = Claim
        fields = ["status"]


class PhotoUploadForm(forms.Form):
    file = forms.FileField(
        widget=forms.ClearableFileInput(attrs={"accept": "image/*,.pdf"})
    )
    caption = forms.CharField(max_length=255, required=False)


def _claim_field():
    return forms.ModelChoiceField(
        queryset=Claim.objects.order_by("-created_at"),
        label="Claim",
        help_text="Which claim does this belong to?",
    )


class PhotoEntryForm(PhotoUploadForm):
    """Standalone photo upload with a claim picker."""

    claim = _claim_field()
    field_order = ["claim", "file", "caption"]


class SurveyForm(forms.ModelForm):
    class Meta:
        model = Survey
        fields = ["surveyor_name", "surveyor_company", "status", "scheduled_for", "notes"]
        widgets = {
            "scheduled_for": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class EmailLogForm(forms.ModelForm):
    class Meta:
        model = EmailLog
        fields = ["direction", "from_address", "to_address", "subject", "body", "sent_at"]
        widgets = {
            "sent_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "body": forms.Textarea(attrs={"rows": 4}),
        }


class SendEmailForm(forms.Form):
    """Compose-and-send an email for a claim, optionally attaching a recovery
    document as a PDF under one of the group companies."""

    ATTACH_CHOICES = [
        ("", "No attachment"),
        ("statement", "Statement"),
        ("lossofuse", "Loss of earnings"),
        ("repairs", "Repairs receipt"),
    ]

    company = forms.ModelChoiceField(
        queryset=Company.objects.filter(is_active=True),
        required=False,
        label="Send under company",
        help_text="Sets the from-address and is required to attach a document",
    )
    to = forms.EmailField(label="To")
    subject = forms.CharField(max_length=255)
    body = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))
    attach = forms.ChoiceField(
        choices=ATTACH_CHOICES, required=False, label="Attach document"
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("attach") and not cleaned.get("company"):
            self.add_error("company", "Choose a company to attach a document.")
        return cleaned


class ReminderForm(forms.ModelForm):
    class Meta:
        model = Reminder
        fields = ["title", "notes", "due_at", "assigned_to"]
        widgets = {
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class GlobalReminderForm(forms.ModelForm):
    """Reminder from the Reminders page — optionally linked to a claim."""

    class Meta:
        model = Reminder
        fields = ["title", "due_at", "claim", "assigned_to", "notes"]
        widgets = {
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["claim"].required = False
        self.fields["claim"].empty_label = "— not linked to a claim —"
        self.fields["claim"].queryset = Claim.objects.order_by("-created_at")


class BillingItemForm(forms.ModelForm):
    class Meta:
        model = BillingItem
        fields = ["description", "category", "amount", "invoice_number", "invoice_date", "status"]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
        }


class SurveyEntryForm(SurveyForm):
    """Standalone survey form with a claim picker."""

    class Meta(SurveyForm.Meta):
        fields = ["claim"] + SurveyForm.Meta.fields


class EmailEntryForm(EmailLogForm):
    """Standalone email log form with a claim picker."""

    class Meta(EmailLogForm.Meta):
        fields = ["claim"] + EmailLogForm.Meta.fields


class BillingEntryForm(BillingItemForm):
    """Standalone billing item form with a claim picker."""

    class Meta(BillingItemForm.Meta):
        fields = ["claim"] + BillingItemForm.Meta.fields
