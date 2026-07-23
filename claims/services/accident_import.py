"""Import the accident register from the master Excel sheet.

Maps the columns of Accident_list_.xlsx onto AccidentRecord rows. Used both by
the web upload and the import_accident_list management command.
"""

from datetime import date, datetime

import openpyxl

from ..models import AccidentRecord

# Sheet header -> model field. Headers are matched case-insensitively and with
# surrounding whitespace stripped.
HEADER_MAP = {
    "date of acc": "date_of_acc",
    "our reg": "our_reg",
    "driver name": "driver_name",
    "tp reg": "tp_reg",
    "vehicle make": "vehicle_make",
    "tp driver name": "tp_driver_name",
    "contact details": "contact_details",
    "tp owner name": "tp_owner_name",
    "tp owner contact no": "tp_owner_contact",
    "tp insurance": "tp_insurance",
    "other tps": "other_tps",
    "tp2 owner name": "tp2_owner_name",
    "tp2 contact no": "tp2_contact",
    "tp2 insurance": "tp2_insurance",
    "report type": "report_type",
    "fault (oi / tp)": "fault",
}


def _to_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def import_accident_list(fileobj, replace=False):
    """Read an .xlsx file object and create AccidentRecord rows.

    Returns (created, skipped). With replace=True the existing register is
    cleared first (a full re-import)."""
    wb = openpyxl.load_workbook(fileobj, read_only=True, data_only=True)
    ws = wb.active

    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        return 0, 0

    # Column index -> model field.
    col_field = {}
    for idx, name in enumerate(header):
        key = _clean(name).lower()
        if key in HEADER_MAP:
            col_field[idx] = HEADER_MAP[key]

    if replace:
        AccidentRecord.objects.all().delete()

    created = skipped = 0
    batch = []
    for raw in rows:
        data = {}
        for idx, field in col_field.items():
            val = raw[idx] if idx < len(raw) else None
            if field == "date_of_acc":
                data[field] = _to_date(val)
            elif field == "fault":
                f = _clean(val).upper()
                data[field] = f if f in ("OI", "TP") else ""
            else:
                data[field] = _clean(val)
        # skip completely blank rows
        if not any(v for v in data.values()):
            skipped += 1
            continue
        batch.append(AccidentRecord(**data))
        created += 1

    AccidentRecord.objects.bulk_create(batch, batch_size=500)
    return created, skipped
