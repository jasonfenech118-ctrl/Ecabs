from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    AccidentPhoto,
    AccidentRecord,
    BillingItem,
    Claim,
    ClaimStatus,
    Company,
    DailyRate,
    EmailLog,
    GarageInvoice,
    GarageInvoiceLine,
    GarageJob,
    GarageJobItem,
    GarageJobLabour,
    GeneralClaim,
    OtherCharge,
    Reminder,
    RepairType,
    Survey,
    Vehicle,
)


@admin.register(DailyRate)
class DailyRateAdmin(admin.ModelAdmin):
    list_display = ("name", "amount", "is_active", "order")
    list_editable = ("amount", "is_active", "order")


@admin.register(RepairType)
class RepairTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "order")
    list_editable = ("is_active", "order")


class GarageJobItemInline(admin.TabularInline):
    model = GarageJobItem
    extra = 0


class GarageJobLabourInline(admin.TabularInline):
    model = GarageJobLabour
    extra = 0


@admin.register(GarageJob)
class GarageJobAdmin(admin.ModelAdmin):
    list_display = ("plate_no", "client", "make", "claim_no", "surveyor",
                    "insurance", "go_ahead", "total", "is_closed", "updated_by")
    list_filter = ("garage", "is_closed", "insurance")
    search_fields = ("plate_no", "client", "claim_no", "surveyor")
    readonly_fields = ("updated_by", "created_at", "updated_at")
    inlines = [GarageJobItemInline, GarageJobLabourInline]


@admin.register(AccidentRecord)
class AccidentRecordAdmin(admin.ModelAdmin):
    list_display = ("date_of_acc", "our_reg", "driver_name", "tp_reg",
                    "vehicle_make", "tp_insurance", "report_type", "fault")
    list_filter = ("fault", "tp_insurance")
    search_fields = ("our_reg", "tp_reg", "driver_name", "tp_owner_name")
    date_hierarchy = "date_of_acc"


@admin.register(ClaimStatus)
class ClaimStatusAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "order")
    list_editable = ("is_active", "order")


@admin.register(GeneralClaim)
class GeneralClaimAdmin(admin.ModelAdmin):
    list_display = ("claim_date", "brand", "our_reg", "driver_name", "insurer",
                    "claim_no", "fault", "estimate_amount", "is_closed")
    list_filter = ("brand", "is_closed", "fault", "insurer")
    search_fields = ("our_reg", "tp_reg", "driver_name", "claim_no")
    date_hierarchy = "claim_date"


class GarageInvoiceLineInline(admin.TabularInline):
    model = GarageInvoiceLine
    extra = 0


@admin.register(GarageInvoice)
class GarageInvoiceAdmin(admin.ModelAdmin):
    list_display = ("invoice_no", "invoice_date", "bill_to", "status", "updated_by")
    list_filter = ("status",)
    search_fields = ("invoice_no", "bill_to", "bill_company_name")
    readonly_fields = ("created_by", "updated_by", "created_at", "updated_at")
    inlines = [GarageInvoiceLineInline]


class OtherChargeInline(admin.TabularInline):
    model = OtherCharge
    extra = 0


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
        "insurance_amount",
        "pay_date",
        "licence_amount",
        "additional_costs",
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
    inlines = [OtherChargeInline, AccidentPhotoInline, SurveyInline, ReminderInline, BillingItemInline]


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
