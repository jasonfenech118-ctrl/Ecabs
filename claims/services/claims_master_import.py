"""Import the master claims register (the "to Jas" tracker) into Claim rows.

The tracker is one row per claim, with the recovery columns the app already
models: labour, spray + material, parts, loss of earnings, others, settlement,
amount paid and offset. Its "TOTAL - Claim Amount" column is the *outstanding*
figure (everything being recovered, less what was paid and offset), which is
what Claim.outstanding_amount computes — so the import checks each row's total
against the app's arithmetic and reports any row that disagrees instead of
silently trusting the sheet.

Nothing is written unless commit=True: the default is a dry run that returns
the same report, so the register can be checked before it touches live data.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import openpyxl

from ..models import Claim, OtherCharge

# Sheet header -> what the column means. Matched case-insensitively on the
# header row, so column order can move without breaking the import.
COLUMNS = {
    "calendar chaser": "chase_on",
    "our reg no": "vehicle_registration",
    "tp reg no": "third_party_registration",
    "accident date": "accident_date",
    "tp claim no": "tp_claim_number",
    "msi claim no": "insurer_claim_number",
    "tp insurance": "third_party_insurer",
    "rar / etars / f2r": "report_type",
    "drivable?": "drivable",
    "survey / photos": "survey_photos",
    "survey in hand?": "survey_in_hand",
    "estimate claim amt": "estimate_amount",
    "claim phase / next action": "next_action",
    "bills sent": "bills_sent_on",
    "invoice no": "invoice_number",
    "labour (net)": "labour_amount",
    "spray + material (net)": "spray_material_amount",
    "parts": "parts_amount",
    "loss of earnings": "loss_of_earnings",
    "loe (days) - daily amt": "loe_breakdown",
    "others": "others",
    "settlement": "settlement_amount",
    "amount paid": "amount_paid",
    "offset": "offset_amount",
    "total - claim amount": "sheet_total",
}

REPORT_TYPES = {
    "etars": Claim.ReportType.ETARS,
    "f 2 r": Claim.ReportType.F2R,
    "f2r": Claim.ReportType.F2R,
    "rar": Claim.ReportType.RAR,
}

# Placeholders staff type when a field doesn't apply.
BLANKS = {"", "-", "--", "n/a", "na", "n\\a", "none", "nothing"}

MONEY_FIELDS = (
    "estimate_amount", "labour_amount", "spray_material_amount", "parts_amount",
    "loss_of_earnings", "others", "settlement_amount", "amount_paid",
    "offset_amount", "sheet_total",
)


def _clean(value):
    return "" if value is None else str(value).strip()


def _blank(value):
    return _clean(value).lower() in BLANKS


def _to_date(value):
    """A real date only — the tracker holds notes in some date columns."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _clean(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _to_money(value):
    """Decimal, or None when the cell can't be read as an amount."""
    if value is None or _clean(value) == "":
        return None
    text = _clean(value).replace("€", "").replace(",", "").replace("EUR", "")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _to_bool(value):
    """Yes/No from cells that often carry a trailing explanation, e.g.
    "NO - CLAIMING FROM 25/04/2026"."""
    text = _clean(value).lower()
    if not text:
        return None
    if text.startswith(("yes", "y ")) or text == "y":
        return True
    if text.startswith(("no", "n ")) or text == "n":
        return False
    return None


def _parse_loe(amount, breakdown):
    """(days, daily_rate) for a loss-of-earnings figure.

    The tracker writes the split as "4 days - € 185.62". Claim stores days and
    a rate and multiplies them, so a bare amount with no split is kept as one
    day at the full amount — that way the claim total still matches the sheet.
    """
    if not amount:
        return None, None
    text = _clean(breakdown)
    days = re.search(r"([\d.]+)\s*day", text, re.I)
    rate = re.search(r"(?:€|eur)\s*([\d,.]+)", text, re.I)
    if days and rate:
        try:
            d = int(Decimal(days.group(1)))
            r = Decimal(rate.group(1).replace(",", "")).quantize(Decimal("0.01"))
            if d > 0:
                return d, r
        except (InvalidOperation, ValueError):
            pass
    return 1, amount


def _natural_key(row):
    """What identifies this claim across re-imports.

    The insurer's claim numbers are unique per claim, so they win. Falling back
    to the plates plus the accident date keeps the two same-day claims on one
    vehicle apart, because their third-party plates differ.
    """
    if row["insurer_claim_number"]:
        return ("msi", row["insurer_claim_number"].upper())
    if row["tp_claim_number"]:
        return ("tp", row["tp_claim_number"].upper())
    return (
        "plate",
        row["vehicle_registration"].upper(),
        row["accident_date"],
        row["third_party_registration"].upper(),
    )


def parse_master_sheet(fileobj):
    """Read the tracker into per-claim dicts, plus the problems found.

    Returns (rows, problems). A row is only a claim when it has a real accident
    date — the sheet ends with dragged-down formula rows and a totals line,
    which are not claims.
    """
    wb = openpyxl.load_workbook(fileobj, read_only=True, data_only=True)
    ws = wb.active
    raw_rows = list(ws.iter_rows(values_only=True))

    # Find the header row (the sheet has a stray value above it).
    header_at, columns = None, {}
    for idx, raw in enumerate(raw_rows[:10]):
        found = {}
        for col, name in enumerate(raw or ()):
            key = _clean(name).lower()
            if key in COLUMNS:
                found[col] = COLUMNS[key]
        if len(found) >= 10:
            header_at, columns = idx, found
            break
    if header_at is None:
        return [], [{"row": None, "issue": "No recognisable header row found."}]

    rows, problems, seen = [], [], {}
    for offset, raw in enumerate(raw_rows[header_at + 1:], start=header_at + 2):
        values = {field: (raw[col] if col < len(raw) else None)
                  for col, field in columns.items()}

        accident_date = _to_date(values.get("accident_date"))
        if accident_date is None:
            # Not a claim: a blank/formula row, the totals line, or a note.
            if any(not _blank(v) for v in values.values()):
                text = _clean(values.get("vehicle_registration"))
                if text and not _clean(values.get("invoice_number")).lower() == "total":
                    problems.append({
                        "row": offset, "severity": "info",
                        "issue": f"Ignored — no accident date ({text[:60]})",
                    })
            continue

        row = {"row": offset, "accident_date": accident_date}
        for field in ("vehicle_registration", "third_party_registration",
                      "tp_claim_number", "insurer_claim_number",
                      "third_party_insurer", "next_action", "invoice_number"):
            value = values.get(field)
            row[field] = "" if _blank(value) else _clean(value)

        row["chase_on"] = _to_date(values.get("chase_on"))
        row["bills_sent_on"] = _to_date(values.get("bills_sent_on"))
        row["drivable"] = _to_bool(values.get("drivable"))
        row["survey_in_hand"] = bool(_to_bool(values.get("survey_in_hand")))

        report = _clean(values.get("report_type")).lower()
        row["report_type"] = REPORT_TYPES.get(report, "")
        if report and not row["report_type"] and not _blank(report):
            row["report_type"] = Claim.ReportType.OTHER

        # Keep the notes staff wrote into the Yes/No and survey columns.
        notes = [_clean(values.get(f)) for f in ("drivable", "survey_photos",
                                                "survey_in_hand", "report_type")]
        row["details"] = " · ".join(n for n in notes if n and not _blank(n))[:255]

        for field in MONEY_FIELDS:
            if field in ("others",):
                continue
            amount = _to_money(values.get(field))
            if values.get(field) not in (None, "") and amount is None:
                problems.append({
                    "row": offset, "severity": "error",
                    "issue": f"{field} is not a number: {values.get(field)!r}",
                })
            row[field] = amount
        row["others"] = _to_money(values.get("others")) or Decimal("0")

        row["loe_days"], row["loe_daily_rate"] = _parse_loe(
            row.get("loss_of_earnings"), values.get("loe_breakdown"))

        if not row["vehicle_registration"]:
            problems.append({"row": offset, "severity": "error",
                             "issue": "No registration number."})

        # The sheet's total must equal what the app will compute.
        money = [row.get(f) or Decimal("0") for f in (
            "labour_amount", "spray_material_amount", "parts_amount")]
        computed = (sum(money, Decimal("0")) + (row.get("loss_of_earnings") or Decimal("0"))
                    + row["others"]
                    - (row.get("amount_paid") or Decimal("0"))
                    - (row.get("offset_amount") or Decimal("0")))
        row["computed_outstanding"] = computed
        sheet_total = row.get("sheet_total")
        if sheet_total is not None and abs(computed - sheet_total) > Decimal("0.05"):
            problems.append({
                "row": offset, "severity": "warning",
                "issue": (f"Sheet total €{sheet_total} but the figures give "
                          f"€{computed} — check {row['vehicle_registration']}."),
            })

        key = _natural_key(row)
        if key in seen:
            problems.append({
                "row": offset, "severity": "warning",
                "issue": f"Same claim as row {seen[key]} ({key[1]}) — imported once.",
            })
            continue
        seen[key] = offset
        row["key"] = key
        rows.append(row)

    return rows, problems


def _match_existing(row):
    """The claim this row already refers to, if any."""
    kind = row["key"][0]
    if kind == "msi":
        return Claim.objects.filter(
            insurer_claim_number__iexact=row["insurer_claim_number"]).first()
    if kind == "tp":
        return Claim.objects.filter(
            tp_claim_number__iexact=row["tp_claim_number"]).first()
    return Claim.objects.filter(
        vehicle_registration__iexact=row["vehicle_registration"],
        accident_date=row["accident_date"],
        third_party_registration__iexact=row["third_party_registration"],
    ).first()


FIELDS_TO_WRITE = (
    "vehicle_registration", "third_party_registration", "accident_date",
    "tp_claim_number", "insurer_claim_number", "third_party_insurer",
    "report_type", "drivable", "survey_in_hand", "estimate_amount",
    "next_action", "bills_sent_on", "invoice_number", "labour_amount",
    "spray_material_amount", "parts_amount", "loe_days", "loe_daily_rate",
    "settlement_amount", "amount_paid", "offset_amount", "chase_on", "details",
)


def import_master_sheet(fileobj, commit=False, user=None):
    """Create or update claims from the tracker.

    Returns a report: counts, and the problems worth a human's attention. With
    commit=False (the default) nothing is written — the same report comes back
    so the sheet can be checked first.
    """
    rows, problems = parse_master_sheet(fileobj)
    created = updated = 0

    for row in rows:
        claim = _match_existing(row)
        if claim is None:
            claim = Claim(created_by=user)
            created += 1
        else:
            updated += 1

        if not commit:
            continue

        for field in FIELDS_TO_WRITE:
            value = row.get(field)
            if field in ("drivable",):
                claim.drivable = value
            elif value is not None:
                setattr(claim, field, value)

        # These are live claims being worked, not drafts, so they get a case
        # number; anything already settled/closed keeps the status it has.
        if claim.status == Claim.Status.DRAFT:
            claim.status = Claim.Status.OPEN
        if claim.submitted_at is None:
            from django.utils import timezone
            claim.submitted_at = timezone.now()
        claim.save()

        # "Others" is recovered through OtherCharge rows — the claim total
        # counts those, not the unused others_amount field.
        if row["others"]:
            OtherCharge.objects.update_or_create(
                claim=claim, description="Others",
                defaults={"amount": row["others"]},
            )

    return {
        "rows": len(rows),
        "created": created,
        "updated": updated,
        "committed": commit,
        "problems": problems,
        "errors": [p for p in problems if p.get("severity") == "error"],
        "warnings": [p for p in problems if p.get("severity") == "warning"],
    }
