from datetime import date

from django.db import migrations

# The ACL Garage worksheet rows, seeded once so the page isn't empty.
ROWS = [
    (date(2026, 5, 3), date(2026, 5, 5), "KLY295", "Mohammed Asif Khaled",
     "Toyota Corolla", "MP26401001819", "Joseph Vella", "Elmo", "5/6/2026", ""),
    (None, date(2026, 6, 5), "RAW100", "Fast Drop",
     "Toyota Dyna", "MC26PC16997", "Maurice Azzo", "Argus", "7/6/2026", ""),
    (date(2026, 5, 8), date(2026, 6, 3), "AQZ641", "Lydon Xerri",
     "Peugeot 107", "MP26409001953", "Joseph Vella", "Elmo", "8/6/2026", ""),
    (date(2026, 6, 1), date(2026, 6, 9), "GBT683", "Alexander Fenech",
     "Toyota Vitz", "MP26409001953", "Marco Borg", "GasanMamo", "9/6/2026", ""),
    (date(2026, 6, 2), date(2026, 6, 4), "SAN257", "Marc Aquilna obo E & J Aquilina LTD",
     "Mercedes GLE Class", "MC26401002384", "Joseph Vella", "Elmo", "9/6/2026", "YES, parts Elmo"),
    (None, date(2026, 6, 9), "CLY134", "Mr Khan",
     "Peugeot 108", "MC26TX02330", "Walter Vella", "Argus", "10/6/2026", ""),
    (date(2025, 12, 5), date(2025, 12, 12), "FLY465", "Dean Xerri",
     "Toyota Corolla", "M20255932MCARU", "Clive Mifsud", "Atlas", "Pending Part", ""),
]


def seed(apps, schema_editor):
    GarageJob = apps.get_model("claims", "GarageJob")
    if GarageJob.objects.filter(garage="acl").exists():
        return
    for (acc, surv, plate, client, make, claim_no, surveyor,
         insurance, report, go_ahead) in ROWS:
        GarageJob.objects.create(
            garage="acl", accident_date=acc, survey_date=surv, plate_no=plate,
            client=client, make=make, claim_no=claim_no, surveyor=surveyor,
            insurance=insurance, report_received=report, go_ahead=go_ahead,
        )


def unseed(apps, schema_editor):
    GarageJob = apps.get_model("claims", "GarageJob")
    GarageJob.objects.filter(garage="acl").delete()


class Migration(migrations.Migration):
    dependencies = [("claims", "0020_garagejob")]
    operations = [migrations.RunPython(seed, unseed)]
