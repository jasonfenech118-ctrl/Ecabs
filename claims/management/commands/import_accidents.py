"""Bulk-import accident/claim records from an Excel (.xlsx) file.

    python manage.py import_accidents path/to/Accident_list_.xlsx --dry-run
    python manage.py import_accidents path/to/Accident_list_.xlsx --status closed

Columns are matched by header name (case-insensitive), so extra columns are
ignored and order doesn't matter. Always run --dry-run first to preview.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from claims.models import Claim

# Spreadsheet header (lower-cased, trimmed)  ->  Claim field
HEADER_MAP = {
    "date of acc": "accident_date",
    "our reg": "vehicle_registration",
    "driver name": "driver_name",
    "tp reg": "third_party_registration",
    "vehicle make": "third_party_vehicle",
    "tp driver name": "third_party_name",
    "contact details": "third_party_phone",
    "tp owner name": "third_party_owner_name",
    "tp owner contact no": "third_party_owner_phone",
    "tp insurance": "third_party_insurer",
    "other tps": "tp2_registration",
    "tp2 owner name": "tp2_owner_name",
    "tp2 contact no": "tp2_owner_phone",
    "tp2 insurance": "tp2_insurer",
    # report_type + fault are handled specially (normalised below)
}

FAULT = {"oi": Claim.Fault.OUR_DRIVER, "tp": Claim.Fault.THIRD_PARTY,
         "shared": Claim.Fault.SHARED, "kfk": Claim.Fault.SHARED}


def _norm(v):
    return "" if v is None else str(v).strip()


def _report_type(raw):
    """Map free text like 'F 2 R' / 'Etars - 307250' to a report_type choice.
    Returns (choice, original_text) — the original is kept in the report ref."""
    text = _norm(raw)
    low = text.lower().replace(" ", "")
    if not text:
        return "", ""
    if low.startswith("etars"):
        return Claim.ReportType.ETARS, text
    if low.startswith("f2r") or "fronttorear" in low:
        return Claim.ReportType.F2R, text
    if low.startswith("rar") or "police" in low:
        return Claim.ReportType.RAR, text
    return Claim.ReportType.OTHER, text


class Command(BaseCommand):
    help = "Import accident/claim rows from an .xlsx file"

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--sheet", default=None, help="Sheet name (default: first)")
        parser.add_argument(
            "--status", default="closed",
            choices=[c for c, _ in Claim.Status.choices],
            help="Status to import rows as (default: closed, for historical loads)",
        )
        parser.add_argument("--dry-run", action="store_true",
                            help="Parse and report, but don't save anything")

    def handle(self, *args, **opts):
        import openpyxl

        wb = openpyxl.load_workbook(opts["path"], data_only=True, read_only=True)
        ws = wb[opts["sheet"]] if opts["sheet"] else wb.worksheets[0]
        rows = ws.iter_rows(values_only=True)

        try:
            header = next(rows)
        except StopIteration:
            raise CommandError("The sheet is empty.")

        # Locate each column by header name.
        cols = {}
        report_col = fault_col = None
        for idx, name in enumerate(header):
            key = _norm(name).lower()
            if key in HEADER_MAP:
                cols[HEADER_MAP[key]] = idx
            elif key == "report type":
                report_col = idx
            elif key.startswith("fault"):
                fault_col = idx

        self.stdout.write(f"Matched {len(cols)} data columns"
                          + (", report type" if report_col is not None else "")
                          + (", fault" if fault_col is not None else ""))

        created = skipped = 0
        preview = []
        with transaction.atomic():
            for row in rows:
                fields = {}
                for field, idx in cols.items():
                    val = row[idx] if idx < len(row) else None
                    if field == "accident_date" and val is not None:
                        fields[field] = val.date() if hasattr(val, "date") else val
                    else:
                        fields[field] = _norm(val)[:250]

                # Skip completely empty rows.
                if not any(fields.get(f) for f in ("vehicle_registration",
                                                   "third_party_registration",
                                                   "accident_date")):
                    skipped += 1
                    continue

                if report_col is not None:
                    rt, original = _report_type(row[report_col] if report_col < len(row) else "")
                    fields["report_type"] = rt
                    if original:
                        fields["police_report_number"] = original[:50]
                if fault_col is not None:
                    fkey = _norm(row[fault_col]).lower() if fault_col < len(row) else ""
                    fields["fault"] = FAULT.get(fkey, Claim.Fault.UNKNOWN)

                fields["status"] = opts["status"]
                if len(preview) < 5:
                    preview.append(fields)
                if not opts["dry_run"]:
                    Claim.objects.create(**fields)
                created += 1

            if opts["dry_run"]:
                transaction.set_rollback(True)

        self.stdout.write("\nPreview of the first rows:")
        for p in preview:
            self.stdout.write(
                f"  {p.get('accident_date','')} | {p.get('vehicle_registration','')} "
                f"| TP {p.get('third_party_registration','')} "
                f"| {p.get('third_party_insurer','')} | fault={p.get('fault','')}"
            )

        verb = "Would import" if opts["dry_run"] else "Imported"
        self.stdout.write(self.style.SUCCESS(
            f"\n{verb} {created} claims (status={opts['status']}); skipped {skipped} empty rows."
        ))
        if opts["dry_run"]:
            self.stdout.write("Dry run — nothing was saved. Re-run without --dry-run to import.")
