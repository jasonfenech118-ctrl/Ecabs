# Vai Drive Claims

Internal insurance claim system for the lease fleet. Staff-only — no customer
access. Built with Django + server-rendered templates + HTMX (no separate
front-end app).

## Features

- **Claims** — central record with reference numbers (`CLM-YYYY-NNNN`), status
  workflow (draft → open → awaiting survey/insurer → settled/closed/rejected)
- **Intake form with auto-save** — drafts save automatically ~2s after you stop
  typing and stay marked *Draft* until submitted
- **Dashboard** — open claims, drafts, overdue reminders, outstanding billing
- **Claims list** — live search-as-you-type across reference, vehicle, driver,
  third party, policy number and location
- **Claim workspace** — tabbed detail page: overview, photos, surveys, emails,
  reminders, billing, and a full audit history (django-simple-history)
- **Photos** — uploaded to Google Drive (one folder per claim); only the Drive
  file ID + metadata are stored in the database. Falls back to local storage
  when Drive isn't configured.
- **Email log** — correspondence recorded against the claim (Gmail API
  auto-pull can populate `gmail_message_id` later)
- **Vehicles** — fleet register: add vehicles, mark them as left the fleet
  (keeps history), or delete mistakes; each vehicle tracks its insurance
  renewal date
- **Renewal reminders** — insurance renewals due within 30 days (or overdue)
  show on the dashboard and the Vehicles page with overdue / due-soon badges
- **Reminders** — per-claim and global follow-up list with overdue flags
- **Billing** — cost items per claim with category, invoice and payment status

## Getting started (Windows)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Then open http://127.0.0.1:8000/ and sign in with the superuser you created.

## Google Drive integration

Uploads go to local `media/` until Drive is configured:

1. `pip install google-api-python-client google-auth`
2. Create a Google Cloud service account with Drive access and download its
   JSON key.
3. Share a Drive folder with the service account's email.
4. Set environment variables:
   - `GOOGLE_DRIVE_CREDENTIALS_FILE` — path to the JSON key
   - `GOOGLE_DRIVE_ROOT_FOLDER_ID` — ID of the shared folder

Each claim gets its own subfolder named after its reference.

## Email

Automated reminders and chase letters are sent from the claim's **Emails** tab
(a recovery document — statement, loss-of-earnings or repairs receipt — can be
attached as a PDF). In development emails print to the console; to send for
real, set the SMTP variables below (for Gmail use an *app password*, not your
account password).

## Moving to PostgreSQL

The database is SQLite by default and switches to PostgreSQL automatically as
soon as `DB_NAME` is set — no code change. Just `pip install psycopg[binary]`,
set the `DB_*` variables (below) and run `migrate`.

## Production deployment

The app hardens itself automatically when `DJANGO_DEBUG=0`: HTTPS redirect,
HSTS, secure/HTTP-only cookies, and it refuses to boot with the throwaway dev
`SECRET_KEY`. Static files are served by WhiteNoise (compressed, cache-hashed).

Deploy steps:

```bash
pip install -r requirements.txt
export DJANGO_DEBUG=0
export DJANGO_SECRET_KEY="$(python -c 'from django.core.management.utils import get_random_secret_key as k; print(k())')"
export DJANGO_ALLOWED_HOSTS="yourdomain.example.com"
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn ecabs.wsgi        # or your host's WSGI runner
```

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_DEBUG` | `0` in production, `1` in dev | `1` |
| `DJANGO_SECRET_KEY` | **required in production** — long random string | dev-only key |
| `DJANGO_ALLOWED_HOSTS` | comma-separated hostnames | `localhost,127.0.0.1` |
| `DJANGO_SECURE_SSL_REDIRECT` | set `0` if a proxy already forces HTTPS | `1` (prod) |
| `DJANGO_HSTS_SECONDS` | HSTS max-age | `3600` (prod) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | comma-separated `https://…` origins | — |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_PORT` | PostgreSQL (activates when `DB_NAME` set) | SQLite |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | SMTP for outbound email | Gmail host, empty creds |
| `EMAIL_USE_TLS` / `EMAIL_USE_SSL` | SMTP transport security | TLS on |
| `DEFAULT_FROM_EMAIL` | from-address for sent mail | `motorclaims@ecabs.com.mt` |
| `DJANGO_EMAIL_BACKEND` | override the auto-selected backend | console in dev, SMTP in prod |
| `GOOGLE_DRIVE_CREDENTIALS_FILE` / `GOOGLE_DRIVE_ROOT_FOLDER_ID` | Google Drive uploads | local `media/` |

A copy of these lives in `.env.example`.

## Tests & checks

```bash
python manage.py test              # unit/integration tests
python tools/system_check.py       # route/template/link wiring audit (seed first)
python manage.py check --deploy    # production security review
```
