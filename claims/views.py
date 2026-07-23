from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseBadRequest
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
    SendEmailForm,
    SurveyEntryForm,
    SurveyForm,
    VehicleForm,
)
from .models import (
    AccidentPhoto,
    AccidentRecord,
    BillingItem,
    Claim,
    Company,
    DailyRate,
    EmailLog,
    GarageInvoice,
    GarageInvoiceLine,
    GarageJob,
    GarageJobItem,
    GarageJobLabour,
    GeneralClaim,
    Reminder,
    RepairLine,
    RepairType,
    Survey,
    Vehicle,
)
from .roles import is_garage_user
from .services import drive
from .services import email as email_service
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


@login_required
def overview(request):
    """A professional one-page picture of the whole ecosystem: the group
    companies, the claim lifecycle, the recovery documents and the background
    engine."""
    companies = Company.objects.filter(is_active=True).order_by("order", "name")
    return render(request, "claims/overview.html", {"companies": companies})


@login_required
def claims_at_fault(request):
    """Register of at-fault claims — where our insured is liable. Not a
    recovery: these close by decision, not when a balance reaches zero."""
    qs = (
        Claim.objects.filter(fault=Claim.Fault.OUR_DRIVER)
        .exclude(status=Claim.Status.DRAFT)
        .order_by("-accident_date", "-id")
    )
    open_claims = [c for c in qs if c.status != Claim.Status.CLOSED]
    closed_claims = [c for c in qs if c.status == Claim.Status.CLOSED]

    def est_total(items):
        return sum((c.estimate_amount or Decimal("0")) for c in items)

    return render(
        request,
        "claims/claims_at_fault.html",
        {
            "open_claims": open_claims,
            "closed_claims": closed_claims,
            "open_estimate": est_total(open_claims),
            "closed_estimate": est_total(closed_claims),
        },
    )


@login_required
@require_POST
def at_fault_update(request, pk):
    """Inline save for an at-fault row: the free-text columns and the estimate,
    plus close/reopen (the manual decision that finalises the claim)."""
    claim = get_object_or_404(Claim, pk=pk)
    action = request.POST.get("action")
    if action == "close":
        claim.status = Claim.Status.CLOSED
    elif action == "reopen":
        claim.status = Claim.Status.OPEN
    else:
        raw = (request.POST.get("estimate_amount") or "").strip()
        if raw:
            try:
                claim.estimate_amount = Decimal(raw)
            except InvalidOperation:
                pass
        else:
            claim.estimate_amount = None
        claim.details = request.POST.get("details", claim.details)
        claim.awaiting_from = request.POST.get("awaiting_from", claim.awaiting_from)
    claim.save()
    return redirect("claims_at_fault")


# --- Panel-beater garage register (ACR Garage) -------------------------------

def _parse_date(raw):
    """Parse a yyyy-mm-dd date from an inline input; blank -> None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    from datetime import datetime

    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


@login_required
def garage_jobs(request):
    """ACR Garage worklist — vehicles sent to the panel beater for repair."""
    qs = GarageJob.objects.filter(garage=GarageJob.Garage.ACL).select_related("updated_by")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(plate_no__icontains=q) | Q(client__icontains=q)
            | Q(make__icontains=q) | Q(claim_no__icontains=q)
            | Q(surveyor__icontains=q) | Q(insurance__icontains=q)
        )
    open_jobs = [j for j in qs if not j.is_closed]
    closed_jobs = [j for j in qs if j.is_closed]

    def sum_total(items):
        return sum((j.effective_total for j in items), Decimal("0"))

    return render(
        request,
        "claims/garage_jobs.html",
        {
            "open_jobs": open_jobs,
            "closed_jobs": closed_jobs,
            "open_total": sum_total(open_jobs),
            "closed_total": sum_total(closed_jobs),
            "garage_name": "ACR Garage",
            "q": q,
        },
    )


@login_required
@require_POST
def garage_job_add(request):
    """Add a blank row to the ACR Garage register (fill it in inline)."""
    GarageJob.objects.create(garage=GarageJob.Garage.ACL, updated_by=request.user)
    return redirect("garage_jobs")


GARAGE_JOB_TEXT_FIELDS = [
    "plate_no", "client", "make", "claim_no", "surveyor", "insurance",
    "report_received", "go_ahead",
]


@login_required
def garage_job_form(request, pk=None):
    """Add or edit an ACR Garage job through a full-page form."""
    job = get_object_or_404(GarageJob, pk=pk) if pk else GarageJob(
        garage=GarageJob.Garage.ACL)
    if request.method == "POST":
        job.accident_date = _parse_date(request.POST.get("accident_date"))
        job.survey_date = _parse_date(request.POST.get("survey_date"))
        for f in GARAGE_JOB_TEXT_FIELDS:
            setattr(job, f, request.POST.get(f, getattr(job, f) or ""))
        raw_total = (request.POST.get("total") or "").strip()
        if raw_total:
            try:
                job.total = Decimal(raw_total)
            except InvalidOperation:
                pass
        else:
            job.total = None
        job.is_closed = request.POST.get("is_closed") == "on"
        job.updated_by = request.user
        job.save()
        _sync_job_items(request, job)
        _sync_job_labour(request, job)
        return redirect("garage_jobs")
    return render(request, "claims/garage_job_form.html", {
        "job": job,
        "repair_types": RepairType.objects.filter(is_active=True),
    })


def _dec(raw):
    try:
        return Decimal(raw.strip()) if (raw or "").strip() else Decimal("0")
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _sync_job_items(request, job):
    """Replace the job's repair items from the submitted in-form rows.

    Each row posts a parallel (item_type, item_new, item_price) triple; a row
    can pick an existing type or type a brand-new one. Empty rows are skipped."""
    type_ids = request.POST.getlist("item_type")
    new_names = request.POST.getlist("item_new")
    prices = request.POST.getlist("item_price")
    job.items.all().delete()
    order = 0
    for i in range(len(prices)):
        new_name = (new_names[i] if i < len(new_names) else "").strip()
        tid = type_ids[i] if i < len(type_ids) else ""
        rt = None
        if new_name:
            rt, _ = RepairType.objects.get_or_create(
                name=new_name, defaults={"order": 100})
        elif tid:
            rt = RepairType.objects.filter(pk=tid).first()
        if rt is None:
            continue
        GarageJobItem.objects.create(job=job, repair_type=rt, price=_dec(prices[i]),
                                     order=order)
        order += 1


def _sync_job_labour(request, job):
    """Replace the job's labour lines from the submitted in-form rows
    (description, hours, rate). Rows with no hours and no rate are skipped."""
    descs = request.POST.getlist("labour_desc")
    hours = request.POST.getlist("labour_hours")
    rates = request.POST.getlist("labour_rate")
    job.labour.all().delete()
    order = 0
    for i in range(len(hours)):
        h = _dec(hours[i] if i < len(hours) else "")
        r = _dec(rates[i] if i < len(rates) else "")
        desc = (descs[i] if i < len(descs) else "").strip()
        if not h and not r and not desc:
            continue
        GarageJobLabour.objects.create(job=job, description=desc, hours=h, rate=r,
                                       order=order)
        order += 1


@login_required
@require_POST
def garage_job_update(request, pk):
    """Inline save / close / reopen / delete for one garage row."""
    job = get_object_or_404(GarageJob, pk=pk)
    action = request.POST.get("action")
    if action == "close":
        job.is_closed = True
        job.updated_by = request.user
        job.save(update_fields=["is_closed", "updated_by", "updated_at"])
    elif action == "reopen":
        job.is_closed = False
        job.updated_by = request.user
        job.save(update_fields=["is_closed", "updated_by", "updated_at"])
    elif action == "delete":
        job.delete()
    else:
        job.accident_date = _parse_date(request.POST.get("accident_date"))
        job.survey_date = _parse_date(request.POST.get("survey_date"))
        job.plate_no = request.POST.get("plate_no", job.plate_no)
        job.client = request.POST.get("client", job.client)
        job.make = request.POST.get("make", job.make)
        job.claim_no = request.POST.get("claim_no", job.claim_no)
        job.surveyor = request.POST.get("surveyor", job.surveyor)
        job.insurance = request.POST.get("insurance", job.insurance)
        job.report_received = request.POST.get("report_received", job.report_received)
        job.go_ahead = request.POST.get("go_ahead", job.go_ahead)
        raw_total = (request.POST.get("total") or "").strip()
        if raw_total:
            try:
                job.total = Decimal(raw_total)
            except InvalidOperation:
                pass
        else:
            job.total = None
        job.updated_by = request.user
        job.save()
    return redirect("garage_jobs")


# --- Garage invoices ---------------------------------------------------------

def _next_invoice_no():
    """Suggest the next INV number from the highest numeric suffix seen."""
    import re

    best = 1052
    for raw in GarageInvoice.objects.values_list("invoice_no", flat=True):
        m = re.search(r"(\d+)", raw or "")
        if m:
            best = max(best, int(m.group(1)))
    return f"INV NO. {best + 1}"


@login_required
def garage_invoices(request):
    """List of garage invoices, with filters.

    A garage user (ACR) only ever sees their own invoices — they are private
    to whoever raised them. An admin (Francis) sees all, but the list defaults
    to *her own* so she isn't shown unrelated invoices; she can switch the
    Owner filter to glance at anyone's."""
    from django.contrib.auth import get_user_model

    garage = is_garage_user(request.user)
    qs = GarageInvoice.objects.select_related("created_by").prefetch_related("lines")

    owners = []
    owner = (request.GET.get("owner") or "").strip()
    if garage:
        qs = qs.filter(created_by=request.user)
        owner = "mine"
    else:
        User = get_user_model()
        owner_ids = (GarageInvoice.objects.values_list("created_by", flat=True)
                     .distinct())
        owners = User.objects.filter(id__in=[i for i in owner_ids if i])
        # Default: only her own, so unrelated invoices don't clutter the view.
        if owner == "":
            owner = "mine"
        if owner == "mine":
            qs = qs.filter(created_by=request.user)
        elif owner == "all":
            pass
        elif owner.isdigit():
            qs = qs.filter(created_by_id=int(owner))

    status = (request.GET.get("status") or "").strip()
    if status in dict(GarageInvoice.Status.choices):
        qs = qs.filter(status=status)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(invoice_no__icontains=q) | Q(bill_to__icontains=q)
            | Q(bill_company_name__icontains=q)
        )

    return render(request, "claims/garage_invoices.html", {
        "invoices": qs,
        "is_garage": garage,
        "owners": owners,
        "owner": owner,
        "status": status,
        "q": q,
        "statuses": GarageInvoice.Status.choices,
    })


@login_required
def impersonate_garage(request):
    """Admin-only: start viewing the app as the garage user (Mario)."""
    from django.contrib import messages
    from django.contrib.auth import get_user_model

    from .middleware import IMPERSONATE_KEY
    from .roles import GARAGE_GROUP

    if not (request.user.is_staff or request.user.is_superuser):
        return redirect("home")
    target = (get_user_model().objects.filter(groups__name=GARAGE_GROUP)
              .exclude(is_staff=True).order_by("id").first())
    if not target:
        messages.error(request, "No garage user exists yet. Create one with "
                                "make_garage_user first.")
        return redirect("garage_jobs")
    request.session[IMPERSONATE_KEY] = target.pk
    return redirect("garage_jobs")


@login_required
def stop_impersonate(request):
    """Return the admin to their own account."""
    from .middleware import IMPERSONATE_KEY

    request.session.pop(IMPERSONATE_KEY, None)
    return redirect("home")


@login_required
def garage_metrics(request):
    """At-a-glance metrics for the ACR Garage: invoices and worklist."""
    garage = is_garage_user(request.user)
    inv_qs = GarageInvoice.objects.prefetch_related("lines")
    if garage:
        inv_qs = inv_qs.filter(created_by=request.user)
    invoices = list(inv_qs)

    total_invoiced = sum((i.balance_due for i in invoices), Decimal("0"))
    by_status = []
    for value, label in GarageInvoice.Status.choices:
        subset = [i for i in invoices if i.status == value]
        by_status.append({
            "label": label, "value": value, "count": len(subset),
            "total": sum((i.balance_due for i in subset), Decimal("0")),
        })
    outstanding = sum((i.balance_due for i in invoices
                       if i.status != GarageInvoice.Status.PAID), Decimal("0"))

    jobs = list(GarageJob.objects.filter(garage=GarageJob.Garage.ACL))
    open_jobs = [j for j in jobs if not j.is_closed]
    closed_jobs = [j for j in jobs if j.is_closed]
    jobs_value = sum((j.total or Decimal("0") for j in jobs), Decimal("0"))

    # Job value by insurer (top rows).
    by_insurer = {}
    for j in jobs:
        key = j.insurance.strip() or "—"
        by_insurer[key] = by_insurer.get(key, Decimal("0")) + (j.total or Decimal("0"))
    insurer_rows = sorted(by_insurer.items(), key=lambda kv: kv[1], reverse=True)[:8]

    return render(request, "claims/garage_metrics.html", {
        "is_garage": garage,
        "invoice_count": len(invoices),
        "total_invoiced": total_invoiced,
        "outstanding": outstanding,
        "by_status": by_status,
        "open_jobs": len(open_jobs),
        "closed_jobs": len(closed_jobs),
        "jobs_total": len(jobs),
        "jobs_value": jobs_value,
        "insurer_rows": insurer_rows,
    })


@login_required
@require_POST
def garage_job_to_invoice(request, pk):
    """Create a new invoice pre-filled from a worklist job (its repairs go on
    the invoice), owned privately by whoever raised it."""
    job = get_object_or_404(GarageJob, pk=pk)
    inv = GarageInvoice.objects.create(
        invoice_no=_next_invoice_no(),
        bill_to=job.client,
        created_by=request.user,
        updated_by=request.user,
    )
    items = list(job.items.all())
    labour = list(job.labour.all())
    order = 0
    if items or labour:
        # One invoice line per itemised repair (type + price)…
        for it in items:
            GarageInvoiceLine.objects.create(
                invoice=inv,
                line_date=job.accident_date if order == 0 else None,
                reg_no=job.plate_no if order == 0 else "",
                description=it.repair_type.name,
                amount=it.price or Decimal("0"),
                order=order,
            )
            order += 1
        # …and one per labour line (hours × rate).
        for lb in labour:
            GarageInvoiceLine.objects.create(
                invoice=inv,
                line_date=job.accident_date if order == 0 else None,
                reg_no=job.plate_no if order == 0 else "",
                description=lb.label,
                unit_price=lb.rate,
                amount=lb.cost,
                order=order,
            )
            order += 1
    else:
        GarageInvoiceLine.objects.create(
            invoice=inv,
            line_date=job.accident_date,
            reg_no=job.plate_no,
            description=job.make or "Repairs",
            amount=job.total or Decimal("0"),
            order=0,
        )
    return redirect("garage_invoice_edit", pk=inv.pk)


@login_required
@require_POST
def garage_invoice_new(request):
    """Start a new invoice with sensible defaults, then edit it."""
    inv = GarageInvoice.objects.create(
        invoice_no=_next_invoice_no(),
        created_by=request.user,
        updated_by=request.user,
    )
    return redirect("garage_invoice_edit", pk=inv.pk)


def _get_owned_invoice(request, pk):
    """Fetch an invoice, but a garage user may only reach their own."""
    inv = get_object_or_404(GarageInvoice, pk=pk)
    if is_garage_user(request.user) and inv.created_by_id != request.user.id:
        return None
    return inv


@login_required
def garage_invoice_edit(request, pk):
    """Edit an invoice: header fields plus inline repair lines."""
    inv = _get_owned_invoice(request, pk)
    if inv is None:
        return redirect("garage_invoices")
    if request.method == "POST":
        for f in ("issuer_name", "payable_to", "issuer_contact", "issuer_vat",
                  "issuer_email", "invoice_no", "bill_to", "bill_contact_name",
                  "bill_company_name", "bill_address", "bill_email", "bill_vat",
                  "remarks"):
            setattr(inv, f, request.POST.get(f, getattr(inv, f)))
        inv.invoice_date = _parse_date(request.POST.get("invoice_date"))
        for f in ("discount_pct", "vat_rate"):
            raw = (request.POST.get(f) or "").strip()
            if raw:
                try:
                    setattr(inv, f, Decimal(raw))
                except InvalidOperation:
                    pass
        status = request.POST.get("status")
        if status in dict(GarageInvoice.Status.choices):
            inv.status = status
        # An admin can reassign the invoice's owner (e.g. to Mario).
        if not is_garage_user(request.user):
            owner_id = (request.POST.get("owner") or "").strip()
            if owner_id.isdigit():
                from django.contrib.auth import get_user_model
                owner = get_user_model().objects.filter(pk=int(owner_id)).first()
                if owner:
                    inv.created_by = owner
        inv.updated_by = request.user
        inv.save()
        return redirect("garage_invoice_edit", pk=inv.pk)

    owners = []
    if not is_garage_user(request.user):
        from django.contrib.auth import get_user_model
        owners = get_user_model().objects.order_by("username")
    return render(request, "claims/garage_invoice_edit.html", {
        "inv": inv, "owners": owners, "is_garage": is_garage_user(request.user),
    })


@login_required
@require_POST
def garage_invoice_line_add(request, pk):
    """Add a blank repair line to the invoice."""
    inv = _get_owned_invoice(request, pk)
    if inv is None:
        return redirect("garage_invoices")
    last = inv.lines.order_by("-order").first()
    GarageInvoiceLine.objects.create(invoice=inv, order=(last.order + 1) if last else 0)
    inv.updated_by = request.user
    inv.save(update_fields=["updated_by", "updated_at"])
    return redirect("garage_invoice_edit", pk=inv.pk)


@login_required
@require_POST
def garage_invoice_line_update(request, pk, line_pk):
    """Inline save or delete of one repair line."""
    inv = _get_owned_invoice(request, pk)
    if inv is None:
        return redirect("garage_invoices")
    line = get_object_or_404(GarageInvoiceLine, pk=line_pk, invoice=inv)
    if request.POST.get("action") == "delete":
        line.delete()
    else:
        line.line_date = _parse_date(request.POST.get("line_date"))
        line.reg_no = request.POST.get("reg_no", line.reg_no)
        line.description = request.POST.get("description", line.description)
        for f in ("unit_price", "amount"):
            raw = (request.POST.get(f) or "").strip()
            if raw:
                try:
                    setattr(line, f, Decimal(raw))
                except InvalidOperation:
                    pass
            elif f == "unit_price":
                line.unit_price = None
            else:
                line.amount = Decimal("0")
        line.save()
    inv.updated_by = request.user
    inv.save(update_fields=["updated_by", "updated_at"])
    return redirect("garage_invoice_edit", pk=inv.pk)


@login_required
def garage_invoice_pdf(request, pk):
    """Render the invoice as a PDF matching the ACR Garage layout."""
    inv = _get_owned_invoice(request, pk)
    if inv is None:
        return redirect("garage_invoices")
    from .services.garage_invoice_pdf import build_garage_invoice_pdf

    pdf = build_garage_invoice_pdf(inv)
    resp = HttpResponse(pdf, content_type="application/pdf")
    name = (inv.invoice_no or "invoice").replace(" ", "_").replace(".", "")
    resp["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    return resp


# --- Accident list (master register) -----------------------------------------

ACCIDENT_FIELDS = [
    "date_of_acc", "our_reg", "driver_name", "tp_reg", "vehicle_make",
    "tp_driver_name", "contact_details", "tp_owner_name", "tp_owner_contact",
    "tp_insurance", "other_tps", "tp2_owner_name", "tp2_contact",
    "tp2_insurance", "report_type", "fault",
]


@login_required
def accident_list(request):
    """Searchable, paginated view of the full accident register."""
    from django.core.paginator import Paginator

    q = (request.GET.get("q") or "").strip()
    qs = AccidentRecord.objects.all()
    if q:
        qs = qs.filter(
            Q(our_reg__icontains=q) | Q(tp_reg__icontains=q)
            | Q(driver_name__icontains=q) | Q(tp_driver_name__icontains=q)
            | Q(tp_owner_name__icontains=q) | Q(tp_insurance__icontains=q)
            | Q(vehicle_make__icontains=q) | Q(report_type__icontains=q)
        )
    paginator = Paginator(qs, 50)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "claims/accident_list.html", {
        "page": page, "q": q, "total": paginator.count,
    })


@login_required
def accident_record_edit(request, pk=None):
    """Add or edit one accident record via a full form."""
    record = get_object_or_404(AccidentRecord, pk=pk) if pk else AccidentRecord()
    if request.method == "POST":
        record.date_of_acc = _parse_date(request.POST.get("date_of_acc"))
        for f in ACCIDENT_FIELDS:
            if f == "date_of_acc":
                continue
            if f == "fault":
                val = (request.POST.get("fault") or "").strip().upper()
                record.fault = val if val in ("OI", "TP") else ""
            else:
                setattr(record, f, request.POST.get(f, getattr(record, f) or ""))
        record.save()
        return redirect("accident_list")
    return render(request, "claims/accident_record_form.html", {"record": record})


@login_required
@require_POST
def accident_record_delete(request, pk):
    get_object_or_404(AccidentRecord, pk=pk).delete()
    return redirect("accident_list")


@login_required
@require_POST
def accident_list_upload(request):
    """Bulk-load the register from an uploaded .xlsx file."""
    from django.contrib import messages

    from .services.accident_import import import_accident_list

    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Choose an Excel (.xlsx) file to upload.")
        return redirect("accident_list")
    replace = request.POST.get("replace") == "on"
    try:
        created, skipped = import_accident_list(f, replace=replace)
    except Exception as exc:  # noqa: BLE001 — surface any parse error to the user
        messages.error(request, f"Could not import that file: {exc}")
        return redirect("accident_list")
    messages.success(
        request,
        f"Imported {created} record{'' if created == 1 else 's'}"
        + (f" (skipped {skipped} blank rows)" if skipped else "")
        + (" — existing register replaced." if replace else "."),
    )
    return redirect("accident_list")


# --- General claims listing (e.g. Firefly) -----------------------------------

GENERAL_CLAIM_TEXT_FIELDS = [
    "brand", "our_reg", "driver_name", "tp_reg", "vehicle_make", "insurer",
    "claim_no", "details", "status_note",
]


@login_required
def general_claims(request):
    """General-claims listing (Firefly). Open/closed sections, searchable."""
    qs = GeneralClaim.objects.select_related("updated_by")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(our_reg__icontains=q) | Q(tp_reg__icontains=q)
            | Q(driver_name__icontains=q) | Q(insurer__icontains=q)
            | Q(claim_no__icontains=q) | Q(vehicle_make__icontains=q)
            | Q(details__icontains=q) | Q(status_note__icontains=q)
        )
    open_rows = [c for c in qs if not c.is_closed]
    closed_rows = [c for c in qs if c.is_closed]

    def est(items):
        return sum((c.estimate_amount or Decimal("0")) for c in items)

    return render(request, "claims/general_claims.html", {
        "open_rows": open_rows, "closed_rows": closed_rows,
        "open_estimate": est(open_rows), "closed_estimate": est(closed_rows),
        "q": q,
    })


@login_required
def general_claim_edit(request, pk=None):
    """Add or edit a general claim through a full-page form."""
    row = get_object_or_404(GeneralClaim, pk=pk) if pk else GeneralClaim()
    if request.method == "POST":
        row.claim_date = _parse_date(request.POST.get("claim_date"))
        for f in GENERAL_CLAIM_TEXT_FIELDS:
            setattr(row, f, request.POST.get(f, getattr(row, f) or ""))
        val = (request.POST.get("fault") or "").strip().upper()
        row.fault = val if val in ("OI", "TP") else ""
        raw = (request.POST.get("estimate_amount") or "").strip()
        if raw:
            try:
                row.estimate_amount = Decimal(raw)
            except InvalidOperation:
                pass
        else:
            row.estimate_amount = None
        row.is_closed = request.POST.get("is_closed") == "on"
        row.updated_by = request.user
        row.save()
        return redirect("general_claims")
    return render(request, "claims/general_claim_form.html", {"row": row})


@login_required
@require_POST
def general_claim_toggle(request, pk):
    """Close or reopen a general claim from the list."""
    row = get_object_or_404(GeneralClaim, pk=pk)
    row.is_closed = request.POST.get("action") == "close"
    row.updated_by = request.user
    row.save(update_fields=["is_closed", "updated_by", "updated_at"])
    return redirect("general_claims")


@login_required
@require_POST
def general_claim_delete(request, pk):
    get_object_or_404(GeneralClaim, pk=pk).delete()
    return redirect("general_claims")


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


@login_required
def metrics(request):
    """System-wide metrics — a high-level KPI section across the whole app."""
    now = timezone.now()
    claims = Claim.objects.all()
    open_statuses = [
        Claim.Status.OPEN, Claim.Status.AWAITING_SURVEY, Claim.Status.AWAITING_INSURER,
    ]
    open_claims = list(claims.filter(status__in=open_statuses))
    closed_statuses = [Claim.Status.SETTLED, Claim.Status.CLOSED, Claim.Status.REJECTED]

    # At-fault (our insured liable)
    atf = claims.filter(fault=Claim.Fault.OUR_DRIVER).exclude(status=Claim.Status.DRAFT)
    atf_open = [c for c in atf if c.status != Claim.Status.CLOSED]
    atf_estimate = sum((c.estimate_amount or Decimal("0")) for c in atf_open)

    # ACR Garage
    jobs = list(GarageJob.objects.filter(garage=GarageJob.Garage.ACL))
    invoices = list(GarageInvoice.objects.prefetch_related("lines"))
    garage_invoiced = sum((i.balance_due for i in invoices), Decimal("0"))
    garage_outstanding = sum((i.balance_due for i in invoices
                              if i.status != GarageInvoice.Status.PAID), Decimal("0"))

    # Claim status breakdown
    status_labels = dict(Claim.Status.choices)
    status_breakdown = [
        {"label": status_labels.get(row["status"], row["status"]), "n": row["n"]}
        for row in claims.values("status").annotate(n=Count("id")).order_by("-n")
    ]

    cards = {
        "claims_total": claims.count(),
        "claims_open": len(open_claims),
        "claims_closed": claims.filter(status__in=closed_statuses).count(),
        "claims_drafts": claims.filter(status=Claim.Status.DRAFT).count(),
        "new_this_month": claims.filter(created_at__year=now.year,
                                        created_at__month=now.month).count(),
        "recovery_outstanding": sum((c.outstanding_amount for c in open_claims), Decimal("0")),
        "atf_open": len(atf_open),
        "atf_estimate": atf_estimate,
        "bills_outstanding": BillingItem.objects.filter(
            status__in=[BillingItem.Status.PENDING, BillingItem.Status.INVOICED]
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0"),
        "overdue_reminders": Reminder.objects.filter(
            completed_at__isnull=True, due_at__lt=now).count(),
        "accident_records": AccidentRecord.objects.count(),
        "garage_open_jobs": len([j for j in jobs if not j.is_closed]),
        "garage_jobs_total": len(jobs),
        "garage_invoiced": garage_invoiced,
        "garage_outstanding": garage_outstanding,
        "garage_invoices": len(invoices),
    }
    return render(request, "claims/metrics.html", {
        "c": cards, "status_breakdown": status_breakdown,
    })


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
        subject, body = email_service.default_chase_message(claim)
        context.setdefault(
            "send_form",
            SendEmailForm(initial={
                "to": claim.insurer_email,
                "subject": subject,
                "body": body,
            }),
        )
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
def email_send(request, pk):
    """Compose-and-send an email for a claim, optionally attaching a recovery
    document PDF. The sent message is recorded in EmailLog either way."""
    claim = get_object_or_404(Claim, pk=pk)
    form = SendEmailForm(request.POST)
    send_error = None
    if form.is_valid():
        cd = form.cleaned_data
        company = cd.get("company")
        attachments = []
        if cd.get("attach") and company:
            built = email_service.document_pdf(claim, company, cd["attach"])
            if built:
                name, content = built
                attachments.append((name, content, "application/pdf"))
        from_email = company.email if company and company.email else None
        try:
            email_service.send_claim_email(
                claim,
                to=cd["to"],
                subject=cd["subject"],
                body=cd["body"],
                from_email=from_email,
                user=request.user,
                attachments=attachments,
            )
            # Success: fall through to a fresh emails tab showing the new entry.
            context = _tab_context(request, claim, "emails")
            return render(request, "claims/partials/tab_emails.html", context)
        except Exception as exc:  # SMTP/backend failure — keep the draft open
            send_error = f"Could not send email: {exc}"

    context = _tab_context(request, claim, "emails")
    context["send_form"] = form
    context["send_open"] = True
    context["send_error"] = send_error
    return render(request, "claims/partials/tab_emails.html", context)


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


@login_required
def audit_trail(request):
    """System-wide audit trail: every change to claims and vehicles, newest
    first — who did it, when, and which fields changed. Backed by
    django-simple-history, so it's a faithful record, not a re-derivation."""
    from itertools import chain

    LIMIT = 200
    ACTIONS = {"+": "created", "~": "updated", "-": "deleted"}

    who = request.GET.get("user", "").strip()
    kind = request.GET.get("kind", "").strip()  # 'claim' | 'vehicle' | ''

    claim_qs = Claim.history.select_related("history_user")
    veh_qs = Vehicle.history.select_related("history_user")
    if who:
        claim_qs = claim_qs.filter(history_user__username=who)
        veh_qs = veh_qs.filter(history_user__username=who)

    sources = []
    if kind in ("", "claim"):
        sources.append(list(claim_qs[:LIMIT]))
    if kind in ("", "vehicle"):
        sources.append(list(veh_qs[:LIMIT]))
    records = sorted(chain(*sources), key=lambda r: r.history_date, reverse=True)[:LIMIT]

    live_claims = set(Claim.objects.values_list("pk", flat=True))
    live_vehicles = set(Vehicle.objects.values_list("pk", flat=True))

    entries = []
    for rec in records:
        if rec.instance_type is Claim:
            label = rec.reference or f"Claim #{rec.id}"
            url = reverse("claim_detail", args=[rec.id]) if rec.id in live_claims else None
            kind_label = "Claim"
        else:
            label = rec.registration or f"Vehicle #{rec.id}"
            url = reverse("vehicle_edit", args=[rec.id]) if rec.id in live_vehicles else None
            kind_label = "Vehicle"
        changed = []
        if rec.history_type == "~" and rec.prev_record is not None:
            try:
                changed = [c.field for c in rec.diff_against(rec.prev_record).changes]
            except Exception:
                changed = []
        entries.append({
            "record": rec,
            "kind": kind_label,
            "label": label,
            "url": url,
            "action": ACTIONS.get(rec.history_type, rec.history_type),
            "changed": changed,
        })

    from django.contrib.auth import get_user_model

    context = {
        "entries": entries,
        "users": get_user_model().objects.order_by("username"),
        "who": who,
        "kind": kind,
        "limit": LIMIT,
    }
    return render(request, "claims/audit_trail.html", context)


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

    net = claim.repairs_receipt_net
    vat = (net * Decimal("0.18")).quantize(Decimal("0.01"))
    pdf_url = dict((key, name) for key, _, name in DOCUMENT_TYPES)[doc]
    # Existing repair-line costs, keyed by repair-type id, to pre-fill the form.
    line_costs = {rl.repair_type_id: rl.cost for rl in claim.repair_lines.all()}
    repair_rows = [
        {"rt": rt, "checked": rt.pk in line_costs, "cost": line_costs.get(rt.pk)}
        for rt in RepairType.objects.filter(is_active=True)
    ]
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
            "lines": claim.invoice_lines(),
            "total": claim.total_claim_amount,
            "repairs_net": net,
            "repairs_vat": vat,
            "repairs_total": net + vat,
            "repair_rows": repair_rows,
            "repair_lines": claim.repair_lines.select_related("repair_type").all(),
        },
    )


def _repairs_redirect(request, claim):
    """Back to the repairs document, keeping the chosen company."""
    url = reverse("claim_documents", args=[claim.pk]) + "?doc=repairs"
    company = request.POST.get("company", "")
    if company:
        url += f"&company={company}"
    return redirect(url)


@login_required
@require_POST
def claim_set_repair_lines(request, pk):
    """Save the itemised repair lines: for each ticked repair type, a line with
    its net cost. Multiple types allowed; VAT is added on the receipt."""
    claim = get_object_or_404(Claim, pk=pk)
    claim.repair_lines.all().delete()
    order = 0
    for rt in RepairType.objects.filter(is_active=True):
        if request.POST.get(f"line_{rt.pk}"):
            raw = (request.POST.get(f"cost_{rt.pk}") or "").strip()
            try:
                cost = Decimal(raw) if raw else Decimal("0")
            except InvalidOperation:
                cost = Decimal("0")
            RepairLine.objects.create(claim=claim, repair_type=rt, cost=cost, order=order)
            order += 1
    return _repairs_redirect(request, claim)


@login_required
@require_POST
def repair_type_add(request, pk):
    """Add a new repair type to the maintained list (for future selection)."""
    claim = get_object_or_404(Claim, pk=pk)
    name = (request.POST.get("name") or "").strip()
    if name:
        RepairType.objects.get_or_create(name=name, defaults={"order": 100})
    return _repairs_redirect(request, claim)


def _manual_invoice_date(request):
    """The invoice/document date is typed in by the user (?date=YYYY-MM-DD);
    it is never auto-filled. Returns a date or None."""
    from datetime import date

    raw = (request.GET.get("date") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@login_required
def claim_invoice_pdf(request, pk):
    """Return the statement as a PDF that opens inline in the browser."""
    from django.http import HttpResponse

    from .services.invoice_pdf import build_invoice_pdf

    claim = get_object_or_404(Claim, pk=pk)
    company = get_object_or_404(Company, pk=request.GET.get("company"))
    pdf = build_invoice_pdf(claim, company, invoice_date=_manual_invoice_date(request))
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
    pdf = build_repairs_pdf(claim, company, invoice_date=_manual_invoice_date(request))
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
    pdf = build_lou_pdf(claim, company, invoice_date=_manual_invoice_date(request))
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
