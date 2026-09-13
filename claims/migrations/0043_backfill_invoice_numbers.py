import re

from django.db import migrations


def backfill_invoice_numbers(apps, schema_editor):
    GarageInvoice = apps.get_model("claims", "GarageInvoice")
    NumberSequence = apps.get_model("claims", "NumberSequence")

    # Find the current high-water mark across all invoices.
    highest = 1052
    for raw in GarageInvoice.objects.values_list("invoice_no", flat=True):
        m = re.search(r"(\d+)", raw or "")
        if m:
            highest = max(highest, int(m.group(1)))

    # Backfill any invoices that have no number (blank or whitespace-only).
    blanks = (GarageInvoice.objects
              .filter(invoice_no="")
              .order_by("id"))
    for inv in blanks:
        highest += 1
        inv.invoice_no = f"INV NO. {highest}"
        inv.save(update_fields=["invoice_no"])

    # Persist the high-water mark so future calls never reuse a number.
    NumberSequence.objects.update_or_create(
        key="garage_invoice", defaults={"last_value": highest}
    )


class Migration(migrations.Migration):

    dependencies = [
        ("claims", "0042_numbersequence"),
    ]

    operations = [
        migrations.RunPython(backfill_invoice_numbers, migrations.RunPython.noop),
    ]
