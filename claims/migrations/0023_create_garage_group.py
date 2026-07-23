from django.db import migrations

GROUP = "Garage"


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP)


def remove_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("claims", "0022_garagejob_total_garagejob_updated_by"),
        ("auth", "0001_initial"),
    ]
    operations = [migrations.RunPython(create_group, remove_group)]
