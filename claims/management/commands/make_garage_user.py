"""Create (or update) a garage-only user, e.g. the ACL Garage owner Mario.

    python manage.py make_garage_user mario --password "SomePass123"

The user is placed in the "Garage" group, which limits them to the ACL Garage
page only. Run again to reset the password. Omit --password to be prompted.
"""

import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from claims.roles import GARAGE_GROUP


class Command(BaseCommand):
    help = "Create or update a garage-only user and add them to the Garage group."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--password", default=None)
        parser.add_argument("--full-name", default="", help="Display name, e.g. 'Mario'")

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
        # Garage users are ordinary users — never staff/superuser, so they get
        # no admin access and stay confined to their section.
        user.is_staff = False
        user.is_superuser = False
        full_name = opts["full_name"].strip()
        if full_name:
            parts = full_name.split(None, 1)
            user.first_name = parts[0]
            user.last_name = parts[1] if len(parts) > 1 else ""
        user.save()

        group, _ = Group.objects.get_or_create(name=GARAGE_GROUP)
        user.groups.add(group)

        verb = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            "%s garage user '%s' — access limited to the ACL Garage page." % (verb, username)
        ))
