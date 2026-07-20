from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    AccidentPhoto,
    BillingItem,
    Claim,
    Company,
    EmailLog,
    Reminder,
    Survey,
    Vehicle,
)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "vat_no", "bank_name", "is_active", "order")
    list_editable = ("order", "is_active")


@admin.register(Vehicle)
class VehicleAdmin(SimpleHistoryAdmin):
    list_display = (
        "registration",
        "make_model",
        "status",
        "insurance_due",
        "retired_on",
    )
    list_filter = ("status",)
    search_fields = ("registration", "make_model")


class AccidentPhotoInline(admin.TabularInline):
    model = AccidentPhoto
    extra = 0
    readonly_fields = ("uploaded_by", "uploaded_at")


class SurveyInline(admin.TabularInline):
    model = Survey
    extra = 0


class ReminderInline(admin.TabularInline):
    model = Reminder
    extra = 0


class BillingItemInline(admin.TabularInline):
    model = BillingItem
    extra = 0


@admin.register(Claim)
class ClaimAdmin(SimpleHistoryAdmin):
    list_display = (
        "reference",
        "case_ref",
        "status",
        "vehicle_registration",
        "accident_date",
        "insurer",
        "created_at",
    )
    list_filter = ("status", "fault", "urgent", "report_type", "insurer")
    search_fields = (
        "reference",
        "vehicle_registration",
        "third_party_name",
        "third_party_registration",
        "third_party_owner_name",
        "tp_claim_number",
        "insurer_claim_number",
        "policy_number",
        "accident_location",
    )
    date_hierarchy = "accident_date"
    readonly_fields = ("reference", "created_at", "updated_at", "submitted_at")
    inlines = [AccidentPhotoInline, SurveyInline, ReminderInline, BillingItemInline]


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("subject", "direction", "claim", "from_address", "to_address", "sent_at")
    list_filter = ("direction",)
    search_fields = ("subject", "from_address", "to_address", "claim__reference")


@admin.register(Reminder)
class ReminderAdmin(admin.ModelAdmin):
    list_display = ("title", "claim", "due_at", "assigned_to", "is_auto", "completed_at")
    list_filter = ("assigned_to", "is_auto")
    search_fields = ("title", "claim__reference")


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ("surveyor_name", "claim", "status", "scheduled_for", "completed_on")
    list_filter = ("status",)


@admin.register(BillingItem)
class BillingItemAdmin(admin.ModelAdmin):
    list_display = ("description", "claim", "category", "amount", "status", "invoice_number")
    list_filter = ("category", "status")


@admin.register(AccidentPhoto)
class AccidentPhotoAdmin(admin.ModelAdmin):
    list_display = ("file_name", "claim", "uploaded_by", "uploaded_at")
    search_fields = ("file_name", "claim__reference")
