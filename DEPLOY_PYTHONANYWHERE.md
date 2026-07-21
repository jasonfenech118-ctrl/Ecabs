# Putting Vai Drive Claims online (PythonAnywhere)

A step-by-step guide to run the **real** app (saves data, sends email) so you
and your colleague can log in and use it. Aimed at PythonAnywhere because it's
the cheapest and simplest host for a small internal tool.

Replace `USERNAME` everywhere below with your PythonAnywhere username.

---

## 0. Before you start — two things to have ready

1. **A PythonAnywhere plan.** The free plan works for *everything except
   sending email* (free accounts can't reach Gmail's mail server). To send the
   chase/recovery emails you need the **"Hacker" plan (~US$5/month)**. You can
   start free and upgrade later.
2. **A Gmail app password** (only needed for sending email). On the Gmail
   account you'll send from: turn on 2-Step Verification, then Google Account →
   Security → App passwords → create one for "Mail". You'll get a 16-character
   password — keep it handy.

---

## 1. Open a Bash console and get the code

On the PythonAnywhere dashboard: **Consoles → Bash**. Then run:

```bash
git clone https://github.com/jasonfenech118-ctrl/Ecabs.git
cd Ecabs
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

(If `python3.12` isn't available, use whichever `python3.x` PythonAnywhere
offers — `python3.10`/`3.11` are fine.)

## 2. Create the settings file (.env)

Still in the console:

```bash
cp .env.example .env
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Copy the long random line it prints. Now open `.env` in the editor
(**Files** tab → `Ecabs/.env`) and set at least these:

```
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<paste the random line here>
DJANGO_ALLOWED_HOSTS=USERNAME.pythonanywhere.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://USERNAME.pythonanywhere.com

# Only needed for sending email (paid plan):
EMAIL_HOST_USER=youraddress@gmail.com
EMAIL_HOST_PASSWORD=<the 16-char Gmail app password>
DEFAULT_FROM_EMAIL=motorclaims@ecabs.com.mt
```

Leave the database lines commented out — it uses SQLite, which is fine here.

## 3. Set up the database and static files

Back in the Bash console:

```bash
cd ~/Ecabs
.venv/bin/python manage.py migrate
.venv/bin/python manage.py createsuperuser      # your login for the app
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py seed_companies       # loads the group companies
```

### Two staff = two logins (same app)

Both staff use the **same** web address and just sign in with their own
username/password — you don't need a second server or account. The app records
who changed what, so give each person their own login rather than sharing one.

Create the second login now (or later) — run again and enter their details:

```bash
.venv/bin/python manage.py createsuperuser
```

(Or, once the app is running, add more people from `/admin/` → **Users** →
**Add user**.)

## 4. Create the web app

1. **Web** tab → **Add a new web app** → **Manual configuration** → pick the
   **same Python version** you used for the virtualenv.
2. On the web app's config page, set:
   - **Source code:** `/home/USERNAME/Ecabs`
   - **Working directory:** `/home/USERNAME/Ecabs`
   - **Virtualenv:** `/home/USERNAME/Ecabs/.venv`
3. Click the **WSGI configuration file** link and replace its whole contents
   with:

   ```python
   import os
   import sys

   path = "/home/USERNAME/Ecabs"
   if path not in sys.path:
       sys.path.insert(0, path)

   os.environ["DJANGO_SETTINGS_MODULE"] = "ecabs.settings"

   from django.core.wsgi import get_wsgi_application
   application = get_wsgi_application()
   ```

   (No secrets here — the app reads them from your `.env`.)
4. Click the big green **Reload** button.

Open **https://USERNAME.pythonanywhere.com/** and log in with the superuser you
created. That's it — this is the real, working app.

## 5. Fill in the company details

Log in and go to `/admin/` → **Companies**. Confirm/complete:
- **Fastdrop International** IBAN (currently blank)
- **Vai Drive** bank (Banif vs APS — confirm which)

## Updating later

When there are new changes, in a Bash console:

```bash
cd ~/Ecabs
git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py collectstatic --noinput
```

Then **Web** tab → **Reload**.

---

## Troubleshooting

- **"DisallowedHost" error** → `DJANGO_ALLOWED_HOSTS` in `.env` must be exactly
  `USERNAME.pythonanywhere.com`. Reload after editing.
- **CSS/logo missing** → you didn't run `collectstatic`, or didn't Reload.
- **Email doesn't send** → you're on the free plan (blocks Gmail), or the app
  password is wrong. Check the **Error log** on the Web tab.
- **"Set a strong DJANGO_SECRET_KEY" error** → `DJANGO_SECRET_KEY` is missing or
  still the placeholder in `.env`.
- Any 500 error → the **Error log** link on the Web tab shows exactly what went
  wrong.
