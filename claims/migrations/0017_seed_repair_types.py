from django.db import migrations

STARTERS = ["Bodywork", "Mechanical", "Windscreen", "Spray & paint", "Electrical", "Parts"]


def seed(apps, schema_editor):
    RepairType = apps.get_model("claims", "RepairType")
    for i, name in enumerate(STARTERS):
        RepairType.objects.get_or_create(name=name, defaults={"order": i})


def unseed(apps, schema_editor):
    RepairType = apps.get_model("claims", "RepairType")
    RepairType.objects.filter(name__in=STARTERS).delete()


class Migration(migrations.Migration):
    dependencies = [("claims", "0016_repairtype_claim_repair_type_and_more")]
    operations = [migrations.RunPython(seed, unseed)]
