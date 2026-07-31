"""
Generate a static, clickable preview of the front-end into demo/.

Renders every page through Django's test client using whatever is in the
local database (run `manage.py seed_demo` first), rewrites internal URLs to
flat .html files, and disables htmx so plain links work when the files are
served statically (e.g. on GitHub Pages).

Usage:  python tools/make_static_demo.py
"""

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ecabs.settings")

import django

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402

from claims.models import Claim, Company, Vehicle  # noqa: E402

OUT = ROOT / "demo"
OUT.mkdir(exist_ok=True)
SRC_STATIC = ROOT / "static"

TABS = ["photos", "surveys", "emails", "reminders", "billing", "history"]

BANNER = (
    '<div style="background:#fef3e2;color:#b45309;padding:.5rem 1rem;'
    'font-size:.85rem;text-align:center">Static preview — data entry, '
    "search and auto-save work when the Django server is running.</div>"
)


def build_url_map():
    urls = {
        "/": "index.html",
        "/dashboard/": "dashboard.html",
        "/claims/": "claims.html",
        "/claims/group/open/": "claims-open.html",
        "/claims/group/closed/": "claims-closed.html",
        "/claims/group/overdue/": "claims-overdue.html",
        "/claims/master/": "master.html",
        "/claims/bills/": "bills.html",
        "/data/": "data.html",
        "/maintenance/": "maintenance.html",
        "/vehicles/add/": "vehicle-add.html",
        "/add/photo/": "add-photo.html",
        "/add/email/": "add-email.html",
        "/add/survey/": "add-survey.html",
        "/add/billing/": "add-billing.html",
        "/vehicles/": "vehicles.html",
        "/reminders/": "reminders.html",
        "/accounts/login/": "login.html",
    }
    for pk in Vehicle.objects.values_list("pk", flat=True):
        urls[f"/vehicles/{pk}/"] = f"vehicle-{pk}.html"
    company_pks = list(Company.objects.values_list("pk", flat=True))
    for claim in Claim.objects.all():
        pk = claim.pk
        urls[f"/claims/{pk}/"] = f"claim-{pk}.html"
        urls[f"/claims/{pk}/edit/"] = f"claim-{pk}-edit.html"
        # Unified documents page — one tile per document type, each per company.
        for doc in ("statement", "lossofuse", "repairs"):
            urls[f"/claims/{pk}/documents/?doc={doc}"] = f"claim-{pk}-doc-{doc}.html"
            for co in company_pks:
                urls[f"/claims/{pk}/documents/?doc={doc}&company={co}"] = (
                    f"claim-{pk}-doc-{doc}-co{co}.html"
                )
        for tab in TABS:
            urls[f"/claims/{pk}/tab/{tab}/"] = f"claim-{pk}-{tab}.html"
    # Recovery-document PDFs: link the "Open PDF" buttons to real files.
    for t in pdf_targets():
        urls[t["url"]] = t["filename"]
    # "New claim" can't create records statically — send it to a draft's form.
    draft = Claim.objects.filter(status=Claim.Status.DRAFT).first()
    if draft:
        urls["/claims/new/"] = f"claim-{draft.pk}-edit.html"
    return urls


def pdf_targets():
    """Every recovery-document PDF the preview should render as a static file:
    each applicable document, under each active company."""
    from claims.services.invoice_pdf import (
        build_invoice_pdf,
        build_lou_pdf,
        build_repairs_pdf,
    )

    specs = [
        ("invoice", build_invoice_pdf, lambda c: bool(c.invoice_lines())),
        ("lou", build_lou_pdf, lambda c: bool(c.loe_days)),
        ("repairs", build_repairs_pdf, lambda c: bool(c.repairs_total)),
    ]
    companies = list(Company.objects.filter(is_active=True))
    targets = []
    for claim in Claim.objects.all():
        for endpoint, builder, applies in specs:
            if not applies(claim):
                continue
            for co in companies:
                targets.append({
                    "url": f"/claims/{claim.pk}/{endpoint}.pdf?company={co.pk}",
                    "filename": f"claim-{claim.pk}-{endpoint}-co{co.pk}.pdf",
                    "builder": builder,
                    "claim": claim,
                    "company": co,
                })
    return targets


def bundle_static():
    """Copy the app's CSS/JS/images into demo/static so the folder is fully
    self-contained and works at any URL (project or user Pages site)."""
    dest = OUT / "static"
    if dest.exists():
        shutil.rmtree(dest)
    for sub in ("css", "js", "img"):
        src = SRC_STATIC / sub
        if src.exists():
            shutil.copytree(src, dest / sub)


def rewrite(html, urls):
    # Longest URLs first so /claims/1/tab/x/ wins over /claims/1/.
    for url, target in sorted(urls.items(), key=lambda kv: -len(kv[0])):
        html = html.replace(f'href="{url}"', f'href="{target}"')
        # Django autoescapes & to &amp; in rendered attributes.
        if "&" in url:
            html = html.replace(f'href="{url.replace("&", "&amp;")}"', f'href="{target}"')
    html = html.replace('href="/admin/"', 'href="#"')
    # Drop htmx so anchor hrefs navigate normally in the static preview.
    html = re.sub(r'<script src="[^"]*htmx\.min\.js" defer></script>', "", html)
    # Bundle references: assets live in demo/static, beside the flat html files.
    html = html.replace('"/static/', '"static/')
    # The logout POST has no server to hit — make it a plain link.
    html = re.sub(
        r'<form method="post" action="/accounts/logout/".*?</form>',
        '<a class="link-btn" href="login.html">Sign out</a>',
        html,
        flags=re.S,
    )
    # Any remaining server-absolute href is a dynamic endpoint with no static
    # equivalent (e.g. an autosave/POST target) — neutralise it so nothing 404s.
    html = re.sub(r'href="/[^"]*"', 'href="#"', html)
    # Insert the banner right after the opening body tag.
    html = re.sub(r"(<body[^>]*>)", r"\1" + BANNER, html, count=1)
    return html


def seed_samples(user):
    """Populate a handful of open / closed / overdue claims for the preview only.
    The live app stays empty — this runs against the throwaway preview database."""
    from datetime import date, timedelta
    from decimal import Decimal

    from django.utils import timezone

    from claims.models import Vehicle, VehicleAdditionalCost, VehicleCost

    if Claim.objects.exclude(status=Claim.Status.DRAFT).exists():
        return
    today = timezone.localdate()

    # A small fleet spread across two companies with insurance paid in different
    # months, so the "Monthly cost breakdown — for budgeting" panel has data.
    fleet = [
        # registration, make/model, owner, year, insurance, pay_date, licence, additionals
        ("ECB101", "Toyota Corolla Hybrid", "Vai Drive", today.year,
         Decimal("620.00"), today.replace(day=8), Decimal("120.00"),
         [(Decimal("85.00"), "Service", today.replace(day=12))]),
        ("ECB214", "Peugeot 108", "Vai Drive", today.year,
         Decimal("540.00"), today.replace(day=8), Decimal("100.00"), []),
        ("ECB330", "Kymco Agility 125i", "Fastdrop International", today.year,
         Decimal("310.00"), (today.replace(day=1) - timedelta(days=15)).replace(day=20),
         Decimal("65.00"), [(Decimal("45.00"), "Tyres", today.replace(day=3))]),
        ("ECB407", "Peugeot 108", "Fastdrop International", today.year,
         Decimal("560.00"), (today.replace(day=1) - timedelta(days=15)).replace(day=20),
         Decimal("100.00"), []),
    ]
    for reg, model, owner, year, ins, pay, lic, extras in fleet:
        vehicle, _ = Vehicle.objects.get_or_create(
            registration=reg,
            defaults={"make_model": model, "owner": owner},
        )
        cost, _ = VehicleCost.objects.get_or_create(
            vehicle=vehicle, year=year,
            defaults={"insurance_amount": ins, "pay_date": pay, "licence_amount": lic},
        )
        for amount, note, when in extras:
            VehicleAdditionalCost.objects.get_or_create(
                cost=cost, note=note, defaults={"amount": amount, "date_added": when})

    S = Claim.Status
    F = Claim.Fault
    samples = [
        # Open
        dict(status=S.OPEN, vehicle_registration="ECB101",
             accident_date=date(2026, 6, 12), accident_location="Tower Road, Sliema",
             third_party_registration="ABC123", third_party_name="John Smith",
             third_party_insurer="Mapfre Middlesea", insurer="Atlas Insurance",
             fault=F.THIRD_PARTY, chase_on=today + timedelta(days=6),
             next_action="Awaiting survey report", tp_claim_number="C34-277147",
             estimate_amount=Decimal("1400.00"),
             bills_sent_on=today, invoice_number="100050",
             labour_amount=Decimal("288.90"), spray_material_amount=Decimal("218.70"),
             parts_amount=Decimal("874.54"), loe_days=14, loe_daily_rate=Decimal("35.00"),
             _others=[("Wheel alignment", "45.00"), ("Windscreen repair", "120.50")]),
        dict(status=S.AWAITING_INSURER, vehicle_registration="ECB214",
             accident_date=date(2026, 6, 3), accident_location="Aldo Moro Road, Marsa",
             third_party_registration="DEF456", third_party_insurer="GasanMamo",
             insurer="Mapfre Middlesea", fault=F.THIRD_PARTY,
             chase_on=today + timedelta(days=3), next_action="O/S payment from MSI",
             tp_claim_number="C34-262527", estimate_amount=Decimal("700.00"),
             bills_sent_on=today.replace(day=1), invoice_number="100052",
             amount_paid=Decimal("240.00"), loe_days=3, loe_daily_rate=Decimal("40.00"),
             labour_amount=Decimal("150.00"), parts_amount=Decimal("420.00")),
        dict(status=S.AWAITING_SURVEY, vehicle_registration="ECB330",
             accident_date=date(2026, 5, 21), accident_location="Coast Road, Bahar ic-Caghaq",
             insurer="Atlas Insurance", fault=F.UNKNOWN,
             next_action="Book surveyor"),
        # Overdue (open, chase date passed)
        dict(status=S.OPEN, vehicle_registration="ECB407",
             accident_date=date(2026, 5, 8), accident_location="Valletta Road, Luqa",
             third_party_registration="GHI789", third_party_name="Peter Borg",
             third_party_insurer="Elmo Insurance", insurer="GasanMamo",
             fault=F.THIRD_PARTY, urgent=True, chase_on=today - timedelta(days=4),
             next_action="Chase Elmo — no response",
             bills_sent_on=today.replace(day=1) + timedelta(days=6), invoice_number="100055",
             labour_amount=Decimal("310.00"), parts_amount=Decimal("905.40"),
             amount_paid=Decimal("200.00")),
        dict(status=S.AWAITING_INSURER, vehicle_registration="ECB512",
             accident_date=date(2026, 4, 27), accident_location="St Anne Street, Floriana",
             third_party_registration="JKL012", insurer="Atlas Insurance",
             fault=F.THIRD_PARTY, chase_on=today - timedelta(days=11),
             next_action="O/S payment from Argus", parts_amount=Decimal("640.00")),
        # Closed / finished
        dict(status=S.SETTLED, vehicle_registration="ECB101",
             accident_date=date(2026, 3, 14), insurer="Mapfre Middlesea",
             fault=F.THIRD_PARTY, settlement_amount=Decimal("1250.00"),
             amount_paid=Decimal("1250.00"), labour_amount=Decimal("500.00"),
             parts_amount=Decimal("750.00")),
        dict(status=S.CLOSED, vehicle_registration="ECB214",
             accident_date=date(2026, 2, 2), insurer="Atlas Insurance", fault=F.OUR_DRIVER),
        dict(status=S.REJECTED, vehicle_registration="ECB330",
             accident_date=date(2025, 12, 19), insurer="GasanMamo", fault=F.SHARED,
             next_action="Rejected — no third-party details"),
    ]
    from claims.models import OtherCharge

    for data in samples:
        others = data.pop("_others", [])
        claim = Claim.objects.create(created_by=user, submitted_at=timezone.now(), **data)
        for i, (desc, amt) in enumerate(others):
            OtherCharge.objects.create(claim=claim, description=desc, amount=Decimal(amt), order=i)


def main():
    # A throwaway login for rendering, and one blank draft so the
    # "+ New claim" button leads to the empty intake form.
    user, _ = get_user_model().objects.get_or_create(username="preview")
    if user.first_name != "Vai Drive":
        user.first_name, user.last_name = "Vai Drive", "Staff"
        user.save()
    from django.core.management import call_command

    call_command("seed_companies")
    from claims.models import DailyRate

    if not DailyRate.objects.exists():
        for i, (n, a) in enumerate([("Standard car", "35.00"), ("Van", "45.00"),
                                    ("Motorcycle", "20.00")]):
            DailyRate.objects.create(name=n, amount=a, order=i)
    seed_samples(user)
    if not Claim.objects.filter(status=Claim.Status.DRAFT).exists():
        Claim.objects.create(created_by=user)
    urls = build_url_map()

    anon = Client()
    page = anon.get("/accounts/login/")
    (OUT / "login.html").write_text(rewrite(page.content.decode(), urls))

    client = Client()
    client.force_login(user)
    for url, filename in urls.items():
        if not filename.endswith(".html"):
            continue  # PDFs are written separately below
        if filename == "login.html" or url == "/claims/new/":
            continue
        response = client.get(url)
        if response.status_code != 200:
            print(f"skip {url} ({response.status_code})")
            continue
        (OUT / filename).write_text(rewrite(response.content.decode(), urls))
        print(f"{url} -> demo/{filename}")

    # Render the recovery-document PDFs as real files.
    pdfs = 0
    for t in pdf_targets():
        (OUT / t["filename"]).write_bytes(t["builder"](t["claim"], t["company"]))
        pdfs += 1

    bundle_static()
    print(f"\nWrote {len(list(OUT.glob('*.html')))} pages + {pdfs} PDFs to demo/")


if __name__ == "__main__":
    main()
