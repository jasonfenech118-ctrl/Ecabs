"""Import the accident register from an Excel file into AccidentRecord.

    python manage.py import_accident_list Accident_list_.xlsx
    python manage.py import_accident_list Accident_list_.xlsx --replace

--replace clears the existing register first (a clean re-import).
"""

from django.core.management.base import BaseCommand, CommandError

from claims.services.accident_import import import_accident_list


class Command(BaseCommand):
    help = "Bulk-load the accident register from an .xlsx file."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--replace", action="store_true",
                            help="Clear the register before importing.")

    def handle(self, *args, **opts):
        path = opts["path"]
        try:
            with open(path, "rb") as fh:
                created, skipped = import_accident_list(fh, replace=opts["replace"])
        except FileNotFoundError:
            raise CommandError(f"File not found: {path}")
        self.stdout.write(self.style.SUCCESS(
            f"Imported {created} records (skipped {skipped} blank rows)."
        ))
