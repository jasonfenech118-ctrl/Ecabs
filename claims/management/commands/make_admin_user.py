"""Create (or update) the main admin/owner user, e.g. Frances.

    python manage.py make_admin_user francis --password "SomePass123"

An admin user is a Django superuser: full access to every page and to the
Django admin. Run again to reset the password. Omit --password to be prompted.
"""

import getpass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update the main admin/owner (superuser) login."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--password", default=None)
        parser.add_argument("--email", default="")
        parser.add_argument("--full-name", default="", help="Display name, e.g. 'Frances'")

    def handle(self, *args, **opts):
        username = opts["username"].strip()
        if not username:
            raise CommandError("A username is required.")

        password = opts["password"]
        if not password:
            password = getpass.getpass("Password for %s: " % username)
        if not password:
            raise CommandError("A password is required.")

        User = get_user_model()
        user, created = User.objects.get_or_create(username=username)
        user.set_password(password)
        # Full owner access — staff (Django admin) and superuser (everything).
        user.is_staff = True
        user.is_superuser = True
        if opts["email"]:
            user.email = opts["email"].strip()
        full_name = opts["full_name"].strip()
        if full_name:
            parts = full_name.split(None, 1)
            user.first_name = parts[0]
            user.last_name = parts[1] if len(parts) > 1 else ""
        user.save()

        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            "%s admin/owner '%s' — full access to the whole system." % (verb, username)
        ))
