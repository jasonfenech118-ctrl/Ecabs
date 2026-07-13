# eCabs Claims

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

## Moving to PostgreSQL

The `DATABASES` block in `ecabs/settings.py` contains a ready-made PostgreSQL
configuration in a comment — swap it in, `pip install psycopg[binary]`, set the
`DB_*` environment variables and run `migrate`.

## Tests

```bat
python manage.py test
```
