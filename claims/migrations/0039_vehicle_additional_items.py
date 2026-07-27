import django.db.models.deletion
from django.db import migrations, models


def copy_additional(apps, schema_editor):
    """Move each year's single additional_costs value into an additional-item
    row so nothing is lost when the flat field is removed."""
    VehicleCost = apps.get_model("claims", "VehicleCost")
    VehicleAdditionalCost = apps.get_model("claims", "VehicleAdditionalCost")
    for c in VehicleCost.objects.all():
        if c.additional_costs:
            VehicleAdditionalCost.objects.create(
                cost=c, amount=c.additional_costs, note="Additional")


class Migration(migrations.Migration):

    dependencies = [
        ("claims", "0038_garagejob_model"),
    ]

    operations = [
        migrations.CreateModel(
            name="VehicleAdditionalCost",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=10, verbose_name="Amount (€)")),
                ("note", models.CharField(blank=True, max_length=200, verbose_name="Note")),
                ("date_added", models.DateField(blank=True, null=True, verbose_name="Date added")),
                ("cost", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="additionals", to="claims.vehiclecost")),
            ],
            options={"ordering": ["date_added", "id"]},
        ),
        migrations.RunPython(copy_additional, migrations.RunPython.noop),
        migrations.RemoveField(model_name="vehiclecost", name="additional_costs"),
    ]
