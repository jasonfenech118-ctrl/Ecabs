from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    BillingEntryForm,
    BillingItemForm,
    ClaimForm,
    EmailEntryForm,
    EmailLogForm,
    GlobalReminderForm,
    PhotoEntryForm,
    PhotoUploadForm,
    ReminderForm,
    SurveyEntryForm,
    SurveyForm,
    VehicleForm,
)
from .models import (
    AccidentPhoto,
    BillingItem,
    Claim,
    Company,
    DailyRate,
    EmailLog,
    Reminder,
    Survey,
    Vehicle,
)
from .services import drive
from .services.auto_reminders import sync_auto_reminders

VALID_TABS = {"overview", "photos", "surveys", "emails", "reminders", "billing", "history"}

OPEN_STATUSES = [
    Claim.Status.OPEN,
    Claim.Status.AWAITING_SURVEY,
    Claim.Status.AWAITING_INSURER,
]
FINISHED_STATUSES = [
    Claim.Status.SETTLED,
    Claim.Status.CLOSED,
    Claim.Status.REJECTED,
]


# Sort options for the claim sheets. Each: key, label, DB ordering.
CLAIM_SORTS = [
    ("date_desc", "Accident date — newest first", ["-accident_date", "-created_at"]),
    ("date_asc", "Accident date — oldest first", ["accident_date", "created_at"]),
    ("case", "Case number", ["case_number"]),
    ("reg", "Our reg (A–Z)", ["vehicle_registration"]),
    ("status", "Status", ["status", "-accident_date"]),
]


def _apply_sort(qs, request):
    key = request.GET.get("sort", "date_desc")
    order = next((o for k, _, o in CLAIM_SORTS if k == key), CLAIM_SORTS[0][2])
    return qs.order_by(*order), key


def _compliance_due(limit=None):
    """Upcoming/overdue VRT, licence and insurance renewals for active vehicles,
    soonest first."""
    horizon = timezone.localdate() + timedelta(days=30)
    items = []
    for vehicle in Vehicle.objects.filter(status=Vehicle.Status.ACTIVE):
        for label, due, state in vehicle.compliance_items():
            if due and due <= horizon:
                items.append({"vehicle": vehicle, "label": label, "due": due, "state": state})
    items.sort(key=lambda item: item["due"])
    return items[:limit] if limit else items


# --- Home (front page) ---------------------------------------------------------

@login_required
def home(request):
    sync_auto_reminders()
    now = timezone.now()
    open_statuses = [
        Claim.Status.OPEN,
        Claim.Status.AWAITING_SURVEY,
        Claim.Status.AWAITING_INSURER,
    ]
    counts = {
        "open_claims": Claim.objects.filter(status__in=open_statuses).count(),
        "reminders_due": Reminder.objects.filter(
            completed_at__isnull=True, due_at__lte=now + timedelta(days=1)
        ).count(),
        "vehicles": Vehicle.objects.filter(status=Vehicle.Status.ACTIVE).count(),
    }
    return render(request, "claims/home.html", {"counts": counts})


# --- Dashboard ---------------------------------------------------------------

@login_required
def dashboard(request):
    sync_auto_reminders()
    now = timezone.now()
    claims = Claim.objects.all()
    open_statuses = [
        Claim.Status.OPEN,
        Claim.Status.AWAITING_SURVEY,
        Claim.Status.AWAITING_INSURER,
    ]
    stats = {
        "open": claims.filter(status__in=open_statuses).count(),
        "drafts": claims.filter(status=Claim.Status.DRAFT).count(),
        "new_this_month": claims.filter(
            created_at__year=now.year, created_at__month=now.month
        ).count(),
        "overdue_reminders": Reminder.objects.filter(
            completed_at__isnull=True, due_at__lt=now
        ).count(),
        "billing_outstanding": BillingItem.objects.filter(
            status__in=[BillingItem.Status.PENDING, BillingItem.Status.INVOICED]
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0"),
    }
    compliance_due = _compliance_due(limit=8)
    stats["compliance_due"] = len(_compliance_due())

    open_claims = claims.filter(status__in=open_statuses)
    stats["recovery_outstanding"] = sum(
        (c.outstanding_amount for c in open_claims), Decimal("0")
    )
    today = timezone.localdate()
    chasers = open_claims.filter(
        Q(urgent=True) | Q(chase_on__lte=today + timedelta(days=7)),
    ).order_by("chase_on")[:8]
    status_breakdown = (
        claims.values("status").annotate(n=Count("id")).order_by("-n")
    )
    status_labels = dict(Claim.Status.choices)
    breakdown = [
        {"label": status_labels[row["status"]], "status": row["status"], "n": row["n"]}
        for row in status_breakdown
    ]
    recent_claims = claims.select_related("created_by")[:8]
    upcoming_reminders = (
        Reminder.objects.filter(
            completed_at__isnull=True, due_at__lt=now + timedelta(days=7)
        )
        .select_related("claim", "assigned_to")
        .order_by("due_at")[:8]
    )
    return render(
        request,
        "claims/dashboard.html",
        {
            "stats": stats,
            "breakdown": breakdown,
            "recent_claims": recent_claims,
            "upcoming_reminders": upcoming_reminders,
            "compliance_due": compliance_due,
            "chasers": chasers,
        },
    )


# --- Claim list & search -------------------------------------------------------

def _filtered_claims(request):
    qs = Claim.objects.select_related("created_by")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(
            Q(reference__icontains=q)
            | Q(vehicle_registration__icontains=q)
            | Q(third_party_name__icontains=q)
            | Q(third_party_registration__icontains=q)
            | Q(third_party_owner_name__icontains=q)
            | Q(tp_claim_number__icontains=q)
            | Q(insurer_claim_number__icontains=q)
            | Q(policy_number__icontains=q)
            | Q(accident_location__icontains=q)
        )
    if status:
        qs = qs.filter(status=status)
    flag = request.GET.get("flag", "").strip()
    if flag == "overdue":
        qs = qs.filter(status__in=OPEN_STATUSES, chase_on__lt=timezone.localdate())
    elif flag == "urgent":
        qs = qs.filter(urgent=True)
    return qs


@login_required
def claim_list(request):
    qs, sort = _apply_sort(_filtered_claims(request), request)
    claims = qs[:100]
    context = {
        "claims": claims,
        "statuses": Claim.Status.choices,
        "sorts": CLAIM_SORTS,
        "sort": sort,
        "q": request.GET.get("q", ""),
        "status": request.GET.get("status", ""),
    }
    # HTMX search requests only need the table body swapped.
    if request.headers.get("HX-Request"):
        return render(request, "claims/partials/claim_rows.html", context)
    return render(request, "claims/claim_list.html", context)


@login_required
def claim_group(request, group):
    """Focused tables: open, closed/finished, or overdue claims."""
    today = timezone.localdate()
    if group == "open":
        qs = Claim.objects.filter(status__in=OPEN_STATUSES)
        title = "Open claims"
        subtitle = "Claims still being worked — open, awaiting survey or awaiting insurer"
    elif group == "closed":
        qs = Claim.objects.filter(status__in=FINISHED_STATUSES)
        title = "Closed claims"
        subtitle = "Finished claims — settled, closed or rejected"
    elif group == "overdue":
        qs = Claim.objects.filter(status__in=OPEN_STATUSES, chase_on__lt=today)
        title = "Overdue claims"
        subtitle = "Open claims past their chase date"
    else:
        return HttpResponseBadRequest("Unknown group")
    qs, sort = _apply_sort(qs, request)
    claims = list(qs)
    estimate_total = sum((c.estimate_amount or Decimal("0") for c in claims), Decimal("0"))
    outstanding_total = sum((c.outstanding_amount for c in claims), Decimal("0"))
    return render(
        request,
        "claims/claim_group.html",
        {
            "claims": claims,
            "title": title,
            "subtitle": subtitle,
            "group": group,
            "sorts": CLAIM_SORTS,
            "sort": sort,
            "estimate_total": estimate_total,
            "outstanding_total": outstanding_total,
        },
    )


@login_required
@require_POST
def claim_set_estimate(request, pk):
    """Inline-edit the estimated total bill for a claim from a group table."""
    claim = get_object_or_404(Claim, pk=pk)
    val = request.POST.get("estimate_amount", "").strip()
    try:
        claim.estimate_amount = Decimal(val) if val else None
    except InvalidOperation:
        pass
    claim.save(update_fields=["estimate_amount"])
    return redirect(request.POST.get("next") or reverse("claim_group", args=["open"]))


# --- Intake form with auto-save ------------------------------------------------

@login_required
def claim_new(request):
    """Create an empty draft immediately so auto-save has a record to write to."""
    claim = Claim.objects.create(created_by=request.user)
    return redirect("claim_edit", pk=claim.pk)


@login_required
def claim_edit(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    form = ClaimForm(instance=claim)
    active_vehicles = Vehicle.objects.filter(status=Vehicle.Status.ACTIVE)
    return render(
        request,
        "claims/claim_form.html",
        {
            "claim": claim,
            "form": form,
            "active_vehicles": active_vehicles,
            "daily_rates": DailyRate.objects.filter(is_active=True),
        },
    )


# --- Maintenance: daily rates --------------------------------------------------

@login_required
def maintenance(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        amount = request.POST.get("amount", "")
        if name and amount:
            try:
                DailyRate.objects.create(
                    name=name, amount=Decimal(amount),
                    order=DailyRate.objects.count(),
                )
            except InvalidOperation:
                pass
        return redirect("maintenance")
    return render(request, "claims/maintenance.html", {"rates": DailyRate.objects.all()})


@login_required
@require_POST
def rate_edit(request, pk):
    rate = get_object_or_404(DailyRate, pk=pk)
    rate.name = request.POST.get("name", rate.name).strip() or rate.name
    try:
        rate.amount = Decimal(request.POST.get("amount"))
    except (InvalidOperation, TypeError):
        pass
    rate.is_active = bool(request.POST.get("is_active"))
    rate.save()
    return redirect("maintenance")


@login_required
@require_POST
def rate_delete(request, pk):
    get_object_or_404(DailyRate, pk=pk).delete()
    return redirect("maintenance")


def _sync_other_charges(claim, request):
    """Rebuild the claim's manual 'other' recovery lines from posted rows."""
    descs = request.POST.getlist("other_desc")
    amts = request.POST.getlist("other_amt")
    claim.other_charges.all().delete()
    order = 0
    for desc, amt in zip(descs, amts):
        desc = desc.strip()
        try:
            value = Decimal(amt)
        except (InvalidOperation, TypeError):
            value = None
        if desc or (value and value != 0):
            claim.other_charges.create(
                description=desc, amount=value or Decimal("0"), order=order
            )
            order += 1


@login_required
@require_POST
def claim_autosave(request, pk):
    """Debounced HTMX target: persist the draft and return the save indicator."""
    claim = get_object_or_404(Claim, pk=pk)
    form = ClaimForm(request.POST, instance=claim)
    if form.is_valid():
        form.save()
        _sync_other_charges(claim, request)
        claim.run_workflow()
        return render(
            request,
            "claims/partials/save_status.html",
            {"saved_at": timezone.localtime(), "ok": True},
        )
    return render(
        request,
        "claims/partials/save_status.html",
        {"errors": form.errors, "ok": False},
    )


@login_required
@require_POST
def claim_submit(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    form = ClaimForm(request.POST, instance=claim)
    if form.is_valid():
        claim = form.save()
    _sync_other_charges(claim, request)
    claim.submit()
    claim.run_workflow()
    return redirect("claim_detail", pk=claim.pk)


# --- Claim detail with HTMX tabs -----------------------------------------------

def _tab_context(request, claim, tab):
    context = {"claim": claim, "active_tab": tab}
    if tab == "photos":
        context["photos"] = claim.photos.select_related("uploaded_by")
        context["photo_form"] = PhotoUploadForm()
        context["drive_enabled"] = drive.drive_enabled()
    elif tab == "surveys":
        context["surveys"] = claim.surveys.all()
        context["survey_form"] = SurveyForm()
    elif tab == "emails":
        context["emails"] = claim.emails.all()
        context["email_form"] = EmailLogForm()
    elif tab == "reminders":
        context["claim_reminders"] = claim.reminders.select_related("assigned_to")
        context["reminder_form"] = ReminderForm()
    elif tab == "billing":
        items = claim.billing_items.all()
        context["billing_items"] = items
        context["billing_total"] = items.aggregate(t=Sum("amount"))["t"] or Decimal("0")
        context["billing_form"] = BillingItemForm()
    elif tab == "history":
        records = list(claim.history.select_related("history_user").all()[:50])
        entries = []
        for i, rec in enumerate(records):
            changes = []
            if i + 1 < len(records):
                changes = rec.diff_against(records[i + 1]).changes
            entries.append({"record": rec, "changes": changes})
        context["history_entries"] = entries
    return context


@login_required
def claim_detail(request, pk, tab="overview"):
    claim = get_object_or_404(Claim, pk=pk)
    if tab not in VALID_TABS:
        return HttpResponseBadRequest("Unknown tab")
    context = _tab_context(request, claim, tab)
    context["statuses"] = Claim.Status.choices
    if request.headers.get("HX-Request"):
        return render(request, f"claims/partials/tab_{tab}.html", context)
    return render(request, "claims/claim_detail.html", context)


@login_required
@require_POST
def claim_set_status(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    status = request.POST.get("status")
    if status not in Claim.Status.values:
        return HttpResponseBadRequest("Unknown status")
    claim.status = status
    if status == Claim.Status.OPEN and claim.submitted_at is None:
        claim.submitted_at = timezone.now()
    claim.save()
    return render(
        request,
        "claims/partials/status_control.html",
        {"claim": claim, "statuses": Claim.Status.choices},
    )


# --- Photos ------------------------------------------------------------------

@login_required
@require_POST
def photo_upload(request, pk):
    claim = get_object_or_404(Claim, pk=pk)
    form = PhotoUploadForm(request.POST, request.FILES)
    if form.is_valid():
        f = form.cleaned_data["file"]
        photo = AccidentPhoto(
            claim=claim,
            caption=form.cleaned_data["caption"],
            uploaded_by=request.user,
            file_name=f.name,
            mime_type=f.content_type or "",
            size_bytes=f.size,
        )
        if drive.drive_enabled():
            stored = drive.upload_file(claim, f)
            photo.drive_file_id = stored.drive_file_id
            photo.web_view_link = stored.web_view_link
        else:
            photo.local_file = f
        photo.save()
    return render(
        request,
        "claims/partials/tab_photos.html",
        _tab_context(request, claim, "photos"),
    )


# --- Related records (HTMX inline adds) ----------------------------------------

def _add_related(request, pk, form_class, tab, save_hook=None):
    claim = get_object_or_404(Claim, pk=pk)
    form = form_class(request.POST)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.claim = claim
        if save_hook:
            save_hook(obj)
        obj.save()
        context = _tab_context(request, claim, tab)
    else:
        context = _tab_context(request, claim, tab)
        context[f"{tab[:-1] if tab.endswith('s') else tab}_form"] = form
        context["form_open"] = True
    return render(request, f"claims/partials/tab_{tab}.html", context)


@login_required
@require_POST
def survey_add(request, pk):
    return _add_related(request, pk, SurveyForm, "surveys")


@login_required
@require_POST
def email_add(request, pk):
    return _add_related(
        request, pk, EmailLogForm, "emails",
        save_hook=lambda obj: setattr(obj, "logged_by", request.user),
    )


@login_required
@require_POST
def reminder_add(request, pk):
    return _add_related(request, pk, ReminderForm, "reminders")


@login_required
@require_POST
def billing_add(request, pk):
    return _add_related(request, pk, BillingItemForm, "billing")


@login_required
@require_POST
def reminder_toggle(request, pk):
    reminder = get_object_or_404(Reminder, pk=pk)
    reminder.completed_at = None if reminder.is_done else timezone.now()
    reminder.save()
    if request.POST.get("scope") == "global":
        return render(
            request, "claims/partials/reminder_row.html", {"r": reminder, "scope": "global"}
        )
    return render(
        request,
        "claims/partials/tab_reminders.html",
        _tab_context(request, reminder.claim, "reminders"),
    )


# --- Global reminders page ------------------------------------------------------

@login_required
def reminder_list(request):
    sync_auto_reminders()
    show = request.GET.get("show", "open")
    qs = Reminder.objects.select_related("claim", "assigned_to")
    if show == "open":
        qs = qs.filter(completed_at__isnull=True)
    context = {
        "reminders": qs[:200],
        "show": show,
        "reminder_form": GlobalReminderForm(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "claims/partials/reminder_rows.html", context)
    return render(request, "claims/reminder_list.html", context)


@login_required
@require_POST
def reminder_create(request):
    """Standalone reminder from the Reminders page (claim link optional)."""
    form = GlobalReminderForm(request.POST)
    if form.is_valid():
        form.save()
        return redirect("reminder_list")
    qs = Reminder.objects.select_related("claim", "assigned_to").filter(
        completed_at__isnull=True
    )
    context = {
        "reminders": qs[:200],
        "show": "open",
        "reminder_form": form,
        "form_open": True,
    }
    return render(request, "claims/reminder_list.html", context)


# --- Vehicles ------------------------------------------------------------------

@login_required
def vehicle_list(request):
    qs = Vehicle.objects.all()
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    if q:
        qs = qs.filter(Q(registration__icontains=q) | Q(make_model__icontains=q))
    if status:
        qs = qs.filter(status=status)
    context = {
        "vehicles": qs,
        "q": q,
        "status": status,
        "statuses": Vehicle.Status.choices,
        "form": VehicleForm(),
        "compliance_due": _compliance_due(),
    }
    if request.headers.get("HX-Request"):
        return render(request, "claims/partials/vehicle_rows.html", context)
    return render(request, "claims/vehicle_list.html", context)


@login_required
def vehicle_add(request):
    """Full-page add-vehicle form (linked from the home menu)."""
    if request.method == "POST":
        form = VehicleForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("vehicle_list")
    else:
        form = VehicleForm()
    return render(request, "claims/vehicle_add.html", {"form": form})


@login_required
def vehicle_edit(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    if request.method == "POST":
        form = VehicleForm(request.POST, instance=vehicle)
        if form.is_valid():
            form.save()
            return redirect("vehicle_list")
    else:
        form = VehicleForm(instance=vehicle)
    related_claims = Claim.objects.filter(
        vehicle_registration__iexact=vehicle.registration
    )
    return render(
        request,
        "claims/vehicle_form.html",
        {"vehicle": vehicle, "form": form, "related_claims": related_claims},
    )


@login_required
@require_POST
def vehicle_toggle_status(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    if vehicle.is_active:
        vehicle.retire()
    else:
        vehicle.reactivate()
    return redirect("vehicle_edit", pk=vehicle.pk)


@login_required
@require_POST
def vehicle_delete(request, pk):
    vehicle = get_object_or_404(Vehicle, pk=pk)
    vehicle.delete()
    return redirect("vehicle_list")


# --- Standalone data entry forms -------------------------------------------------

ENTRY_FORMS = {
    "photo": {"form": PhotoEntryForm, "title": "Add photo / document", "tab": "photos"},
    "survey": {"form": SurveyEntryForm, "title": "Add survey", "tab": "surveys"},
    "email": {"form": EmailEntryForm, "title": "Log an email", "tab": "emails"},
    "billing": {"form": BillingEntryForm, "title": "Add billing item", "tab": "billing"},
}


@login_required
def record_add(request, kind):
    """One full-page form per record type, with a claim picker."""
    spec = ENTRY_FORMS.get(kind)
    if spec is None:
        return HttpResponseBadRequest("Unknown form")
    form_class = spec["form"]
    if request.method == "POST":
        form = form_class(request.POST, request.FILES)
        if form.is_valid():
            if kind == "photo":
                claim = form.cleaned_data["claim"]
                f = form.cleaned_data["file"]
                photo = AccidentPhoto(
                    claim=claim,
                    caption=form.cleaned_data["caption"],
                    uploaded_by=request.user,
                    file_name=f.name,
                    mime_type=f.content_type or "",
                    size_bytes=f.size,
                )
                if drive.drive_enabled():
                    stored = drive.upload_file(claim, f)
                    photo.drive_file_id = stored.drive_file_id
                    photo.web_view_link = stored.web_view_link
                else:
                    photo.local_file = f
                photo.save()
            else:
                obj = form.save(commit=False)
                if kind == "email":
                    obj.logged_by = request.user
                obj.save()
                claim = obj.claim
            return redirect("claim_tab", pk=claim.pk, tab=spec["tab"])
    else:
        form = form_class(initial={"claim": request.GET.get("claim")})
    return render(
        request,
        "claims/record_form.html",
        {"form": form, "title": spec["title"], "kind": kind},
    )


# --- Master sheet -----------------------------------------------------------------

def _fmt_date(d):
    return d.strftime("%d/%m/%Y") if d else ""


def _fmt_money(v):
    return f"{v:.2f}" if v is not None else ""


def _fmt_bool(v):
    if v is None:
        return ""
    return "Yes" if v else "No"


MASTER_COLUMNS = [
    ("Case no", lambda c: c.case_ref),
    ("Ref", lambda c: c.reference),
    ("Status", lambda c: c.get_status_display()),
    ("Urgent", lambda c: "URGENT" if c.urgent else ""),
    ("Date of acc", lambda c: _fmt_date(c.accident_date)),
    ("Our reg", lambda c: c.vehicle_registration),
    ("TP number plate", lambda c: c.third_party_registration),
    ("TP vehicle make", lambda c: c.third_party_vehicle),
    ("TP driver name", lambda c: c.third_party_name),
    ("TP driver contact", lambda c: c.third_party_phone),
    ("TP owner name", lambda c: c.third_party_owner_name),
    ("TP owner contact", lambda c: c.third_party_owner_phone),
    ("TP insurance", lambda c: c.third_party_insurer),
    ("TP claim no", lambda c: c.tp_claim_number),
    ("TP2 number plate", lambda c: c.tp2_registration),
    ("TP2 owner", lambda c: c.tp2_owner_name),
    ("TP2 contact", lambda c: c.tp2_owner_phone),
    ("TP2 insurance", lambda c: c.tp2_insurer),
    ("Report type", lambda c: c.get_report_type_display() if c.report_type else ""),
    ("Report ref", lambda c: c.police_report_number),
    ("Fault (OI/TP)", lambda c: c.get_fault_display()),
    ("Drivable", lambda c: _fmt_bool(c.drivable)),
    ("Survey in hand", lambda c: _fmt_bool(c.survey_in_hand)),
    ("Estimate", lambda c: _fmt_money(c.estimate_amount)),
    ("Our insurer", lambda c: c.insurer),
    ("Policy no", lambda c: c.policy_number),
    ("Insurer claim no", lambda c: c.insurer_claim_number),
    ("Bills sent", lambda c: _fmt_date(c.bills_sent_on)),
    ("Invoice no", lambda c: c.invoice_number),
    ("Labour (net)", lambda c: _fmt_money(c.labour_amount)),
    ("Spray + material", lambda c: _fmt_money(c.spray_material_amount)),
    ("Parts", lambda c: _fmt_money(c.parts_amount)),
    ("LOE days", lambda c: c.loe_days or ""),
    ("LOE daily", lambda c: _fmt_money(c.loe_daily_rate)),
    ("Loss of earnings", lambda c: _fmt_money(c.loss_of_earnings)),
    ("Others", lambda c: _fmt_money(c.other_charges_total)),
    ("TOTAL claim", lambda c: _fmt_money(c.total_claim_amount)),
    ("Settlement", lambda c: _fmt_money(c.settlement_amount)),
    ("Amount paid", lambda c: _fmt_money(c.amount_paid)),
    ("Offset", lambda c: _fmt_money(c.offset_amount)),
    ("Outstanding", lambda c: _fmt_money(c.outstanding_amount)),
    ("Next action", lambda c: c.next_action),
    ("Chase on", lambda c: _fmt_date(c.chase_on)),
    ("Created", lambda c: _fmt_date(c.created_at.date())),
]


def _by_accident_desc(claims):
    """Newest accident first; claims with no accident date sort to the end."""
    from datetime import date

    return sorted(claims, key=lambda c: c.accident_date or date.min, reverse=True)


def _month_year_groups(claims):
    """Group claims into month/year sections (newest first) for the sheet."""
    groups = []
    for c in _by_accident_desc(claims):
        if c.accident_date:
            key = (c.accident_date.year, c.accident_date.month)
            label = c.accident_date.strftime("%B %Y")
        else:
            key, label = None, "No accident date"
        if not groups or groups[-1]["key"] != key:
            groups.append({"key": key, "label": label, "rows": []})
        groups[-1]["rows"].append(
            {"pk": c.pk, "cells": [fn(c) for _, fn in MASTER_COLUMNS]}
        )
    return groups


def _status_summary():
    """Counts and outstanding totals per status, plus overdue/urgent/total."""
    claims = list(Claim.objects.all())
    today = timezone.localdate()

    def agg(predicate):
        matched = [c for c in claims if predicate(c)]
        total = sum((c.outstanding_amount for c in matched), Decimal("0"))
        return len(matched), total

    rows = []
    for value, label in Claim.Status.choices:
        count, outstanding = agg(lambda c, v=value: c.status == v)
        rows.append(
            {"label": label, "count": count, "outstanding": outstanding,
             "query": f"status={value}"}
        )
    count, outstanding = agg(
        lambda c: c.status in OPEN_STATUSES and c.chase_on and c.chase_on < today
    )
    rows.append(
        {"label": "Overdue (chase date passed)", "count": count,
         "outstanding": outstanding, "query": "flag=overdue", "highlight": True}
    )
    count, outstanding = agg(lambda c: c.urgent)
    rows.append(
        {"label": "Urgent", "count": count, "outstanding": outstanding,
         "query": "flag=urgent", "highlight": True}
    )
    count, outstanding = agg(lambda c: True)
    rows.append(
        {"label": "Total claims", "count": count, "outstanding": outstanding,
         "query": "", "total": True}
    )
    return rows


@login_required
def master_sheet(request):
    groups = _month_year_groups(_filtered_claims(request))
    return render(
        request,
        "claims/master_sheet.html",
        {
            "headers": [h for h, _ in MASTER_COLUMNS],
            "groups": groups,
            "summary": _status_summary(),
            "statuses": Claim.Status.choices,
            "q": request.GET.get("q", ""),
            "status": request.GET.get("status", ""),
        },
    )


DOCUMENT_TYPES = [
    ("statement", "Statement", "claim_invoice_pdf"),
    ("lossofuse", "Loss of Earnings", "claim_lou_pdf"),
    ("repairs", "Repairs receipt", "claim_repairs_pdf"),
]


@login_required
def claim_documents(request, pk):
    """One page for all recovery documents. Pick the document type (quick-pick
    tiles), then the company (letterhead), then open the PDF."""
    from decimal import Decimal

    claim = get_object_or_404(Claim, pk=pk)
    companies = Company.objects.filter(is_active=True)
    doc = request.GET.get("doc") or "statement"
    if doc not in {key for key, _, _ in DOCUMENT_TYPES}:
        doc = "statement"
    selected = request.GET.get("company")
    company = companies.filter(pk=selected).first() if selected else None

    net = claim.repairs_total
    vat = (net * Decimal("0.18")).quantize(Decimal("0.01"))
    pdf_url = dict((key, name) for key, _, name in DOCUMENT_TYPES)[doc]
    return render(
        request,
        "claims/documents.html",
        {
            "claim": claim,
            "companies": companies,
            "company": company,
            "doc": doc,
            "document_types": DOCUMENT_TYPES,
            "pdf_url": pdf_url,
            "invoice_no": claim.case_ref or claim.reference,
            "invoice_date": claim.bills_sent_on or timezone.localdate(),
            "lines": claim.invoice_lines(),
            "total": claim.total_claim_amount,
            "repairs_net": net,
            "repairs_vat": vat,
            "repairs_total": net + vat,
        },
    )


@login_required
def claim_invoice_pdf(request, pk):
    """Return the statement as a PDF that opens inline in the browser."""
    from django.http import HttpResponse

    from .services.invoice_pdf import build_invoice_pdf

    claim = get_object_or_404(Claim, pk=pk)
    company = get_object_or_404(Company, pk=request.GET.get("company"))
    pdf = build_invoice_pdf(claim, company)
    response = HttpResponse(pdf, content_type="application/pdf")
    ref = (claim.case_ref or claim.reference).replace(" ", "")
    response["Content-Disposition"] = f'inline; filename="statement-{ref}.pdf"'
    return response


@login_required
def claim_repairs_pdf(request, pk):
    from django.http import HttpResponse

    from .services.invoice_pdf import build_repairs_pdf

    claim = get_object_or_404(Claim, pk=pk)
    company = get_object_or_404(Company, pk=request.GET.get("company"))
    pdf = build_repairs_pdf(claim, company)
    response = HttpResponse(pdf, content_type="application/pdf")
    ref = (claim.case_ref or claim.reference).replace(" ", "")
    response["Content-Disposition"] = f'inline; filename="repairs-{ref}.pdf"'
    return response


@login_required
def claim_lou_pdf(request, pk):
    """Return the loss-of-use letter as an inline PDF."""
    from django.http import HttpResponse

    from .services.invoice_pdf import build_lou_pdf

    claim = get_object_or_404(Claim, pk=pk)
    company = get_object_or_404(Company, pk=request.GET.get("company"))
    pdf = build_lou_pdf(claim, company)
    response = HttpResponse(pdf, content_type="application/pdf")
    ref = (claim.case_ref or claim.reference).replace(" ", "")
    response["Content-Disposition"] = f'inline; filename="loss-of-use-{ref}.pdf"'
    return response


@login_required
def data_dashboard(request):
    """Charts: claims by status, lifecycle, by insurer, outstanding, by month."""
    from calendar import month_abbr
    from collections import Counter, defaultdict

    claims = list(Claim.objects.all())
    today = timezone.localdate()

    # KPIs
    open_n = sum(1 for c in claims if c.status in OPEN_STATUSES)
    closed_n = sum(1 for c in claims if c.status in FINISHED_STATUSES)
    draft_n = sum(1 for c in claims if c.status == Claim.Status.DRAFT)
    overdue_n = sum(
        1 for c in claims
        if c.status in OPEN_STATUSES and c.chase_on and c.chase_on < today
    )
    urgent_n = sum(1 for c in claims if c.urgent)
    outstanding = sum((c.outstanding_amount for c in claims), Decimal("0"))

    # Claims by status
    status_counts = Counter(c.status for c in claims)
    by_status = {
        "labels": [label for _v, label in Claim.Status.choices],
        "data": [status_counts.get(v, 0) for v, _l in Claim.Status.choices],
    }

    # Lifecycle (mutually exclusive)
    lifecycle = {
        "labels": ["Open", "Closed", "Draft"],
        "data": [open_n, closed_n, draft_n],
    }

    # Attention
    attention = {
        "labels": ["Open", "Overdue", "Urgent"],
        "data": [open_n, overdue_n, urgent_n],
    }

    # By third-party insurer (count) and outstanding (€), top insurers
    ins_count = Counter(c.third_party_insurer or "(none)" for c in claims)
    ins_out = defaultdict(Decimal)
    for c in claims:
        ins_out[c.third_party_insurer or "(none)"] += c.outstanding_amount
    top_ins = [name for name, _ in ins_count.most_common(8)]
    by_insurer = {"labels": top_ins, "data": [ins_count[n] for n in top_ins]}
    out_ins_sorted = sorted(ins_out.items(), key=lambda kv: kv[1], reverse=True)[:8]
    outstanding_by_insurer = {
        "labels": [n for n, _ in out_ins_sorted],
        "data": [float(v) for _n, v in out_ins_sorted],
    }

    # Claims by accident month — last 12 months
    months = []
    y, m = today.year, today.month
    seq = []
    for _ in range(12):
        seq.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    seq.reverse()
    mcount = Counter(
        (c.accident_date.year, c.accident_date.month) for c in claims if c.accident_date
    )
    by_month = {
        "labels": [f"{month_abbr[mm]} {yy % 100:02d}" for yy, mm in seq],
        "data": [mcount.get((yy, mm), 0) for yy, mm in seq],
    }

    chart_data = {
        "by_status": by_status,
        "lifecycle": lifecycle,
        "attention": attention,
        "by_insurer": by_insurer,
        "outstanding_by_insurer": outstanding_by_insurer,
        "by_month": by_month,
    }
    kpis = {
        "total": len(claims),
        "open": open_n,
        "overdue": overdue_n,
        "outstanding": outstanding,
    }
    return render(
        request, "claims/data.html", {"chart_data": chart_data, "kpis": kpis}
    )


@login_required
def bills_report(request):
    """Bills sent in a chosen month (defaults to the current month), with totals
    and a CSV export — the monthly billing worksheet."""
    import calendar
    from datetime import date

    today = timezone.localdate()
    # Accept separate m + y from the dropdowns, or a legacy month=YYYY-MM
    # (still used by the CSV download link).
    sel_month = request.GET.get("m")
    sel_year = request.GET.get("y")
    raw_month = request.GET.get("month", "")
    try:
        if sel_month and sel_year:
            year, month = int(sel_year), int(sel_month)
        elif raw_month:
            year, month = (int(x) for x in raw_month.split("-"))
        else:
            year, month = today.year, today.month
        date(year, month, 1)
    except (ValueError, TypeError):
        year, month = today.year, today.month
    month_str = f"{year:04d}-{month:02d}"
    months = [(i, calendar.month_name[i]) for i in range(1, 13)]
    years = list(range(today.year - 6, today.year + 2))

    claims = list(
        Claim.objects.filter(
            bills_sent_on__year=year, bills_sent_on__month=month
        ).order_by("third_party_insurer", "bills_sent_on")
    )
    total_claim = sum((c.total_claim_amount for c in claims), Decimal("0"))
    total_paid = sum((c.amount_paid or Decimal("0") for c in claims), Decimal("0"))
    total_out = sum((c.outstanding_amount for c in claims), Decimal("0"))
    label = date(year, month, 1).strftime("%B %Y")

    # Group the month's bills by the insurer being claimed from.
    groups = []
    for c in claims:
        insurer = c.third_party_insurer or "(no insurer recorded)"
        if not groups or groups[-1]["insurer"] != insurer:
            groups.append(
                {"insurer": insurer, "rows": [], "total_claim": Decimal("0"),
                 "total_paid": Decimal("0"), "total_out": Decimal("0")}
            )
        g = groups[-1]
        g["rows"].append(c)
        g["total_claim"] += c.total_claim_amount
        g["total_paid"] += c.amount_paid or Decimal("0")
        g["total_out"] += c.outstanding_amount

    if request.GET.get("format") == "csv":
        import csv

        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="bills-{month_str}.csv"'
        writer = csv.writer(response)
        writer.writerow(
            ["TP insurance", "Case no", "Ref", "Our reg", "Bill sent", "Invoice no",
             "Total claim", "Amount paid", "Outstanding"]
        )
        for g in groups:
            for c in g["rows"]:
                writer.writerow(
                    [g["insurer"], c.case_ref, c.reference, c.vehicle_registration,
                     _fmt_date(c.bills_sent_on), c.invoice_number,
                     _fmt_money(c.total_claim_amount), _fmt_money(c.amount_paid),
                     _fmt_money(c.outstanding_amount)]
                )
            writer.writerow(
                [f"{g['insurer']} — subtotal", "", "", "", "", "",
                 _fmt_money(g["total_claim"]), _fmt_money(g["total_paid"]),
                 _fmt_money(g["total_out"])]
            )
        writer.writerow(
            ["TOTAL", "", "", "", "", "",
             _fmt_money(total_claim), _fmt_money(total_paid), _fmt_money(total_out)]
        )
        return response

    # --- Bills pending: billed claims still outstanding, choosable by insurer ---
    pending_all = [
        c for c in Claim.objects.filter(bills_sent_on__isnull=False)
        if c.outstanding_amount > 0
    ]
    pending_insurers = sorted(
        {c.third_party_insurer or "(no insurer recorded)" for c in pending_all}
    )
    pending_insurer = request.GET.get("pending_insurer", "")
    if pending_insurer:
        pending = [
            c for c in pending_all
            if (c.third_party_insurer or "(no insurer recorded)") == pending_insurer
        ]
    else:
        pending = pending_all
    pending.sort(
        key=lambda c: (c.third_party_insurer or "", c.bills_sent_on or date.min)
    )
    pending_total = sum((c.outstanding_amount for c in pending), Decimal("0"))

    return render(
        request,
        "claims/bills_report.html",
        {
            "groups": groups,
            "claims": claims,
            "month_str": month_str,
            "label": label,
            "months": months,
            "years": years,
            "sel_month": month,
            "sel_year": year,
            "total_claim": total_claim,
            "total_paid": total_paid,
            "total_out": total_out,
            "pending": pending,
            "pending_insurers": pending_insurers,
            "pending_insurer": pending_insurer,
            "pending_total": pending_total,
        },
    )


@login_required
def master_sheet_csv(request):
    import csv

    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="master-sheet.csv"'
    writer = csv.writer(response)
    writer.writerow([h for h, _ in MASTER_COLUMNS])
    for claim in _by_accident_desc(_filtered_claims(request)):
        writer.writerow([fn(claim) for _, fn in MASTER_COLUMNS])
    return response
