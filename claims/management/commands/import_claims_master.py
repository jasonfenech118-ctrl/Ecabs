"""Import the master claims register (the "to Jas" tracker) into Claim rows.

Dry run by default — it reports what it would do and any rows whose figures
don't add up. Pass --commit to actually write.

    python manage.py import_claims_master to_Jas.xlsx
    python manage.py import_claims_master to_Jas.xlsx --commit
"""

from django.core.management.base import BaseCommand, CommandError

from claims.services.claims_master_import import import_master_sheet


class Command(BaseCommand):
    help = "Import the master claims register from an .xlsx tracker."

    def add_arguments(self, parser):
        parser.add_argument("path", help="Path to the .xlsx tracker")
        parser.add_argument(
            "--commit", action="store_true",
            help="Write the claims. Without this it is a dry run.",
        )

    def handle(self, *args, **options):
        path = options["path"]
        try:
            fh = open(path, "rb")
        except OSError as exc:
            raise CommandError(f"Can't open {path}: {exc}")

        with fh:
            report = import_master_sheet(fh, commit=options["commit"])

        self.stdout.write(
            f"{report['rows']} claims read — "
            f"{report['created']} new, {report['updated']} already on file."
        )

        for problem in report["problems"]:
            severity = problem.get("severity", "info")
            line = f"  row {problem['row']}: {problem['issue']}"
            if severity == "error":
                self.stdout.write(self.style.ERROR(line))
            elif severity == "warning":
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(line)

        if report["committed"]:
            self.stdout.write(self.style.SUCCESS("Saved."))
        else:
            self.stdout.write(self.style.WARNING(
                "Dry run — nothing saved. Re-run with --commit to write."))
