from datetime import date

import django.db.models.deletion
from django.db import migrations, models


def copy_costs(apps, schema_editor):
    """Move each vehicle's existing flat cost values into a cost row for the
    current year, so nothing is lost when the flat fields are removed."""
    Vehicle = apps.get_model("claims", "Vehicle")
    VehicleCost = apps.get_model("claims", "VehicleCost")
    year = date.today().year
    for v in Vehicle.objects.all():
        if any([v.insurance_amount, v.pay_date, v.licence_amount, v.additional_costs]):
            VehicleCost.objects.get_or_create(
                vehicle=v, year=year,
                defaults={
                    "insurance_amount": v.insurance_amount,
                    "pay_date": v.pay_date,
                    "licence_amount": v.licence_amount,
                    "additional_costs": v.additional_costs,
                },
            )


class Migration(migrations.Migration):

    dependencies = [
        ("claims", "0036_historicalvehicle_owner_vehicle_owner"),
    ]

    operations = [
        migrations.CreateModel(
            name="VehicleCost",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("year", models.PositiveIntegerField(verbose_name="Year")),
                ("insurance_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Insurance amount (€)")),
                ("pay_date", models.DateField(blank=True, null=True, verbose_name="Insurance pay date")),
                ("licence_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Licence amount (€)")),
                ("additional_costs", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Additional costs (€)")),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="costs", to="claims.vehicle")),
            ],
            options={
                "ordering": ["-year"],
                "unique_together": {("vehicle", "year")},
            },
        ),
        migrations.RunPython(copy_costs, migrations.RunPython.noop),
        migrations.RemoveField(model_name="historicalvehicle", name="additional_costs"),
        migrations.RemoveField(model_name="historicalvehicle", name="insurance_amount"),
        migrations.RemoveField(model_name="historicalvehicle", name="licence_amount"),
        migrations.RemoveField(model_name="historicalvehicle", name="pay_date"),
        migrations.RemoveField(model_name="vehicle", name="additional_costs"),
        migrations.RemoveField(model_name="vehicle", name="insurance_amount"),
        migrations.RemoveField(model_name="vehicle", name="licence_amount"),
        migrations.RemoveField(model_name="vehicle", name="pay_date"),
    ]
