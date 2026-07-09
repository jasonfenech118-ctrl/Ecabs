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
    'font-size:.85rem;text-align:center">Static preview with demo data — '
    "search, auto-save and uploads need the Django server running.</div>"
)


def build_url_map():
    urls = {
        "/": "index.html",
        "/claims/": "claims.html",
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


def main():
    urls = build_url_map()
    user = get_user_model().objects.filter(username="demo").first()
    if user is None:
        raise SystemExit("Run `manage.py seed_demo` first (needs the demo user).")

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
