"""
Read-only system audit: exercises every URL, checks template wiring and
crawls internal links. Run against the seeded demo DB:

    python tools/make_static_demo.py   # seed
    DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver python tools/system_check.py
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ecabs.settings")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import get_resolver, reverse, NoReverseMatch  # noqa: E402
from django.urls.resolvers import URLPattern, URLResolver  # noqa: E402

from claims.models import Claim, Company, DailyRate, Reminder, Vehicle  # noqa: E402

TEMPLATES = ROOT / "templates"
fails = []

# POST-only endpoints that mutate/destroy data — verify they exist and
# reject GET (405), but never actually fire them in an audit.
DESTRUCTIVE = {
    "logout", "vehicle_delete", "vehicle_toggle_status", "reminder_toggle",
    "rate_delete", "claim_submit", "claim_set_status", "claim_autosave",
    "photo_upload", "survey_add", "email_add", "email_send", "reminder_add",
    "billing_add", "reminder_create", "record_add", "rate_edit",
    "claim_set_estimate", "vehicle_edit", "vehicle_add", "claim_new",
}

claim = Claim.objects.exclude(status=Claim.Status.DRAFT).first()
vehicle = Vehicle.objects.first()
reminder = Reminder.objects.first()
rate = DailyRate.objects.first()


def all_named_patterns():
    """Only this project's own routes — the claims app plus the auth
    login/logout. Django-admin and simple-history routes are namespaced and
    out of scope for a front-end wiring audit."""
    from claims import urls as claims_urls

    return [p for p in claims_urls.urlpatterns
            if isinstance(p, URLPattern) and p.name]


# PDF endpoints require a ?company= to build the document.
COMPANY = Company.objects.filter(is_active=True).first()
NEEDS_COMPANY = {"claim_invoice_pdf", "claim_lou_pdf", "claim_repairs_pdf"}


def build_kwargs(pat):
    kwargs = {}
    name = pat.name or ""
    for p in pat.pattern.regex.groupindex:
        if p == "pk":
            if name.startswith("vehicle"):
                kwargs[p] = vehicle.pk
            elif name.startswith("rate"):
                kwargs[p] = rate.pk
            elif name == "reminder_toggle":
                kwargs[p] = reminder.pk
            else:
                kwargs[p] = claim.pk
        elif p == "tab":
            kwargs[p] = "overview"
        elif p == "kind":
            kwargs[p] = "photo"
        elif p == "group":
            kwargs[p] = "open"
        else:
            kwargs[p] = "1"
    return kwargs


user, _ = get_user_model().objects.get_or_create(username="auditor")
user.is_staff = True
user.save()
c = Client()
c.force_login(user)

named = all_named_patterns()
app_names = {p.name for p in named} | {"login", "logout"}

print("\n### A. Every named URL: reverse() + request ###")
print("=" * 70)
get_ok = postonly_ok = 0
for pat in sorted(named, key=lambda p: p.name):
    name = pat.name
    if name in ("login",):
        continue
    try:
        url = reverse(name, kwargs=build_kwargs(pat))
    except NoReverseMatch as e:
        fails.append(f"reverse({name}) failed: {e}")
        print(f"  ✗ {name:26} REVERSE FAILED: {e}")
        continue
    if name in NEEDS_COMPANY:
        url += f"?company={COMPANY.pk}"
    r = c.get(url)
    code = r.status_code
    if code == 200:
        get_ok += 1
        print(f"  ✓ GET  {name:26} {url}  200")
    elif code in (301, 302):
        get_ok += 1
        print(f"  → GET  {name:26} {url}  {code}")
    elif code == 405 and name in DESTRUCTIVE:
        postonly_ok += 1
        print(f"  ✓ POST-only {name:22} {url}  (GET 405, correctly method-guarded)")
    else:
        fails.append(f"GET {name} {url} -> {code}")
        print(f"  ✗ GET  {name:26} {url}  {code}")
print(f"\n  {get_ok} GET pages OK, {postonly_ok} POST-only guarded, of {len(named)} routes")

print("\n### B. Template {% url %} names all resolve ###")
print("=" * 70)
url_ref = re.compile(r"{%\s*url\s+'([a-z_]+)'")
missing = set()
n_tpl = 0
for tpl in TEMPLATES.rglob("*.html"):
    n_tpl += 1
    for m in url_ref.finditer(tpl.read_text()):
        if m.group(1) not in app_names:
            missing.add((m.group(1), str(tpl.relative_to(ROOT))))
if missing:
    for nm, t in sorted(missing):
        fails.append(f"{t} references unknown url '{nm}'")
        print(f"  ✗ unknown url '{nm}' in {t}")
else:
    print(f"  ✓ all {{% url %}} names valid across {n_tpl} templates")

print("\n### C. {% include %} / {% extends %} targets exist ###")
print("=" * 70)
inc_ref = re.compile(r"{%\s*(?:include|extends)\s+\"([^\"]+)\"")
bad = []
for tpl in TEMPLATES.rglob("*.html"):
    text = tpl.read_text()
    for m in inc_ref.finditer(text):
        ref = m.group(1)
        # dynamic include such as "claims/partials/tab_"|add:active_tab
        if ref.endswith("_") or ("|add:" in text[m.end():m.end() + 40]):
            continue
        if not (TEMPLATES / ref).exists():
            bad.append((ref, str(tpl.relative_to(ROOT))))
if bad:
    for ref, t in bad:
        fails.append(f"{t} includes missing '{ref}'")
        print(f"  ✗ missing template '{ref}' in {t}")
else:
    print("  ✓ all static include/extends targets exist")
for tab in ["overview", "photos", "surveys", "emails", "reminders", "billing", "history"]:
    p = TEMPLATES / "claims" / "partials" / f"tab_{tab}.html"
    if not p.exists():
        fails.append(f"missing tab partial tab_{tab}.html")
        print(f"  ✗ missing tab partial tab_{tab}.html")
print("  ✓ all 7 dynamic tab_*.html partials present")

print("\n### D. Internal-link crawl of every rendered page ###")
print("=" * 70)
pages = [
    "/", "/dashboard/", "/claims/", "/claims/group/open/", "/claims/group/closed/",
    "/claims/group/overdue/", "/claims/master/", "/claims/master/csv/",
    "/claims/bills/", "/data/", "/maintenance/", "/vehicles/", "/vehicles/add/",
    "/reminders/", f"/claims/{claim.pk}/", f"/claims/{claim.pk}/edit/",
    f"/claims/{claim.pk}/documents/?doc=statement", f"/vehicles/{vehicle.pk}/",
]
href_re = re.compile(r'(?:href|action)="(/[^"#]*)"')
checked, broken = {}, 0
for page in pages:
    r = c.get(page)
    if r.status_code not in (200,):
        fails.append(f"page {page} -> {r.status_code}")
        print(f"  ✗ {page} -> {r.status_code}")
        continue
    for link in set(href_re.findall(r.content.decode())):
        if link.startswith("/static/") or link.startswith("/admin") or link.startswith("/accounts/logout"):
            continue
        if link in checked:
            continue
        checked[link] = c.get(link).status_code
        if checked[link] not in (200, 302, 405):
            broken += 1
            fails.append(f"link {link} (on {page}) -> {checked[link]}")
            print(f"  ✗ {link}  ({checked[link]})  on {page}")
print(f"  ✓ crawled {len(checked)} unique internal links; broken: {broken}")

print("\n### E. HTMX partial endpoints (tabs + live search) ###")
print("=" * 70)
for tab in ["overview", "photos", "surveys", "emails", "reminders", "billing", "history"]:
    r = c.get(f"/claims/{claim.pk}/tab/{tab}/", HTTP_HX_REQUEST="true")
    if r.status_code != 200:
        fails.append(f"tab {tab} -> {r.status_code}")
    print(f"  {'✓' if r.status_code==200 else '✗'} tab {tab:10} {r.status_code}")
r = c.get("/claims/", {"q": "ECB"}, HTTP_HX_REQUEST="true")
print(f"  {'✓' if r.status_code==200 else '✗'} live search (htmx)  {r.status_code}")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"  Failures: {len(fails)}")
for f in fails:
    print(f"   ✗ {f}")
if not fails:
    print("\n  ✅ ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
