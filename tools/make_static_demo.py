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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ecabs.settings")

import django

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402

from claims.models import Claim, Vehicle  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "demo"
OUT.mkdir(exist_ok=True)

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
    for pk in Claim.objects.values_list("pk", flat=True):
        urls[f"/claims/{pk}/"] = f"claim-{pk}.html"
        urls[f"/claims/{pk}/edit/"] = f"claim-{pk}-edit.html"
        for tab in TABS:
            urls[f"/claims/{pk}/tab/{tab}/"] = f"claim-{pk}-{tab}.html"
    # "New claim" can't create records statically — send it to a draft's form.
    draft = Claim.objects.filter(status=Claim.Status.DRAFT).first()
    if draft:
        urls["/claims/new/"] = f"claim-{draft.pk}-edit.html"
    return urls


def rewrite(html, urls):
    # Longest URLs first so /claims/1/tab/x/ wins over /claims/1/.
    for url, target in sorted(urls.items(), key=lambda kv: -len(kv[0])):
        html = html.replace(f'href="{url}"', f'href="{target}"')
    html = html.replace('href="/admin/"', 'href="#"')
    # Drop htmx so anchor hrefs navigate normally in the static preview.
    html = re.sub(r'<script src="[^"]*htmx\.min\.js" defer></script>', "", html)
    # Static assets live one level up from demo/.
    html = html.replace('"/static/', '"../static/')
    # The logout POST has no server to hit — make it a plain link.
    html = re.sub(
        r'<form method="post" action="/accounts/logout/".*?</form>',
        '<a class="link-btn" href="login.html">Sign out</a>',
        html,
        flags=re.S,
    )
    # Insert the banner right after the opening body tag.
    html = re.sub(r"(<body[^>]*>)", r"\1" + BANNER, html, count=1)
    return html


def seed_samples(user):
    """Populate a handful of open / closed / overdue claims for the preview only.
    The live app stays empty — this runs against the throwaway preview database."""
    from datetime import date, timedelta
    from decimal import Decimal

    from django.utils import timezone

    from claims.models import Vehicle

    if Claim.objects.exclude(status=Claim.Status.DRAFT).exists():
        return
    today = timezone.localdate()
    Vehicle.objects.get_or_create(
        registration="ECB-101",
        defaults={"make_model": "Toyota Corolla Hybrid", "insurance_due": today + timedelta(days=18)},
    )

    S = Claim.Status
    F = Claim.Fault
    samples = [
        # Open
        dict(status=S.OPEN, vehicle_registration="ECB-101",
             accident_date=date(2026, 6, 12), accident_location="Tower Road, Sliema",
             third_party_registration="ABC123", third_party_name="John Smith",
             third_party_insurer="Mapfre Middlesea", insurer="Atlas Insurance",
             fault=F.THIRD_PARTY, chase_on=today + timedelta(days=6),
             next_action="Awaiting survey report",
             bills_sent_on=today, invoice_number="100050",
             labour_amount=Decimal("243.60"), parts_amount=Decimal("800.21")),
        dict(status=S.AWAITING_INSURER, vehicle_registration="ECB-214",
             accident_date=date(2026, 6, 3), accident_location="Aldo Moro Road, Marsa",
             third_party_registration="DEF456", third_party_insurer="GasanMamo",
             insurer="Mapfre Middlesea", fault=F.THIRD_PARTY,
             chase_on=today + timedelta(days=3), next_action="O/S payment from MSI",
             bills_sent_on=today.replace(day=1), invoice_number="100052",
             amount_paid=Decimal("240.00"),
             labour_amount=Decimal("150.00"), parts_amount=Decimal("420.00")),
        dict(status=S.AWAITING_SURVEY, vehicle_registration="ECB-330",
             accident_date=date(2026, 5, 21), accident_location="Coast Road, Bahar ic-Caghaq",
             insurer="Atlas Insurance", fault=F.UNKNOWN,
             next_action="Book surveyor"),
        # Overdue (open, chase date passed)
        dict(status=S.OPEN, vehicle_registration="ECB-407",
             accident_date=date(2026, 5, 8), accident_location="Valletta Road, Luqa",
             third_party_registration="GHI789", third_party_name="Peter Borg",
             third_party_insurer="Elmo Insurance", insurer="GasanMamo",
             fault=F.THIRD_PARTY, urgent=True, chase_on=today - timedelta(days=4),
             next_action="Chase Elmo — no response",
             bills_sent_on=today.replace(day=1) + timedelta(days=6), invoice_number="100055",
             labour_amount=Decimal("310.00"), parts_amount=Decimal("905.40"),
             amount_paid=Decimal("200.00")),
        dict(status=S.AWAITING_INSURER, vehicle_registration="ECB-512",
             accident_date=date(2026, 4, 27), accident_location="St Anne Street, Floriana",
             third_party_registration="JKL012", insurer="Atlas Insurance",
             fault=F.THIRD_PARTY, chase_on=today - timedelta(days=11),
             next_action="O/S payment from Argus", parts_amount=Decimal("640.00")),
        # Closed / finished
        dict(status=S.SETTLED, vehicle_registration="ECB-101",
             accident_date=date(2026, 3, 14), insurer="Mapfre Middlesea",
             fault=F.THIRD_PARTY, settlement_amount=Decimal("1250.00"),
             amount_paid=Decimal("1250.00"), labour_amount=Decimal("500.00"),
             parts_amount=Decimal("750.00")),
        dict(status=S.CLOSED, vehicle_registration="ECB-214",
             accident_date=date(2026, 2, 2), insurer="Atlas Insurance", fault=F.OUR_DRIVER),
        dict(status=S.REJECTED, vehicle_registration="ECB-330",
             accident_date=date(2025, 12, 19), insurer="GasanMamo", fault=F.SHARED,
             next_action="Rejected — no third-party details"),
    ]
    for data in samples:
        Claim.objects.create(created_by=user, submitted_at=timezone.now(), **data)


def main():
    # A throwaway login for rendering, and one blank draft so the
    # "+ New claim" button leads to the empty intake form.
    user, _ = get_user_model().objects.get_or_create(username="preview")
    if user.first_name != "Vai Drive":
        user.first_name, user.last_name = "Vai Drive", "Staff"
        user.save()
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
        if filename == "login.html" or url == "/claims/new/":
            continue
        response = client.get(url)
        if response.status_code != 200:
            print(f"skip {url} ({response.status_code})")
            continue
        (OUT / filename).write_text(rewrite(response.content.decode(), urls))
        print(f"{url} -> demo/{filename}")
    print(f"\nWrote {len(list(OUT.glob('*.html')))} pages to demo/")


if __name__ == "__main__":
    main()
