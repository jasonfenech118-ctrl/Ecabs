from django import forms

from .models import BillingItem, Claim, EmailLog, Reminder, Survey, Vehicle


class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = [
            "registration",
            "make_model",
            "year",
            "acquired_on",
            "insurance_due",
            "notes",
        ]
        widgets = {
            "acquired_on": forms.DateInput(attrs={"type": "date"}),
            "insurance_due": forms.DateInput(attrs={"type": "date"}),
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
            "driver_phone",
            "driver_email",
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
            "excess_amount",
            "drivable",
            "survey_in_hand",
            "estimate_amount",
            "bills_sent_on",
            "invoice_number",
            "labour_amount",
            "spray_material_amount",
            "parts_amount",
            "loe_days",
            "loe_daily_rate",
            "others_amount",
            "settlement_amount",
            "amount_paid",
            "offset_amount",
            "next_action",
            "chase_on",
            "urgent",
        ]
        widgets = {
            "accident_date": forms.DateInput(attrs={"type": "date"}),
            "accident_time": forms.TimeInput(attrs={"type": "time"}),
            "bills_sent_on": forms.DateInput(attrs={"type": "date"}),
            "chase_on": forms.DateInput(attrs={"type": "date"}),
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


class ReminderForm(forms.ModelForm):
    class Meta:
        model = Reminder
        fields = ["title", "notes", "due_at", "assigned_to"]
        widgets = {
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class BillingItemForm(forms.ModelForm):
    class Meta:
        model = BillingItem
        fields = ["description", "category", "amount", "invoice_number", "invoice_date", "status"]
        widgets = {
            "invoice_date": forms.DateInput(attrs={"type": "date"}),
        }
