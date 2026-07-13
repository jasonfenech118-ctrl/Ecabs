"""Refresh automated reminders: `manage.py sync_reminders`.

The web views run this sync on every visit to the home, dashboard and
reminders pages, so scheduling this command is optional — useful mainly if
you want reminders generated even when nobody has the app open.
"""

from django.core.management.base import BaseCommand

from claims.services.auto_reminders import sync_auto_reminders


class Command(BaseCommand):
    help = "Create/refresh automated reminders (insurance renewals, chase dates)"

    def handle(self, *args, **options):
        created, removed = sync_auto_reminders()
        self.stdout.write(
            self.style.SUCCESS(f"Reminders synced: {created} created, {removed} removed")
        )
