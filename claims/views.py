from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    BillingItemForm,
    ClaimForm,
    EmailLogForm,
    GlobalReminderForm,
    PhotoUploadForm,
    ReminderForm,
    SurveyForm,
    VehicleForm,
)
from .models import (
    AccidentPhoto,
    BillingItem,
    Claim,
    EmailLog,
    Reminder,
    Survey,
    Vehicle,
)
from .services import drive
from .services.auto_reminders import sync_auto_reminders

VALID_TABS = {"overview", "photos", "surveys", "emails", "reminders", "billing", "history"}


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
            | Q(driver_name__icontains=q)
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
    return qs


@login_required
def claim_list(request):
    claims = _filtered_claims(request)[:100]
    context = {
        "claims": claims,
        "statuses": Claim.Status.choices,
        "q": request.GET.get("q", ""),
        "status": request.GET.get("status", ""),
    }
    # HTMX search requests only need the table body swapped.
    if request.headers.get("HX-Request"):
        return render(request, "claims/partials/claim_rows.html", context)
    return render(request, "claims/claim_list.html", context)


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
        {"claim": claim, "form": form, "active_vehicles": active_vehicles},
    )


@login_required
@require_POST
def claim_autosave(request, pk):
    """Debounced HTMX target: persist the draft and return the save indicator."""
    claim = get_object_or_404(Claim, pk=pk)
    form = ClaimForm(request.POST, instance=claim)
    if form.is_valid():
        form.save()
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
    claim.submit()
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
@require_POST
def vehicle_add(request):
    form = VehicleForm(request.POST)
    if form.is_valid():
        form.save()
        return redirect("vehicle_list")
    context = {
        "vehicles": Vehicle.objects.all(),
        "q": "",
        "status": "",
        "statuses": Vehicle.Status.choices,
        "form": form,
        "form_open": True,
        "compliance_due": _compliance_due(),
    }
    return render(request, "claims/vehicle_list.html", context)


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
