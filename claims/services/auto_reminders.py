"""
Automated reminders.

sync_auto_reminders() looks at the data and keeps system-generated reminders
in step with it:

- an active vehicle's insurance renewal within the next 60 days
- an open claim's chase-on date

Each source gets a stable auto_key, so running the sync repeatedly never
duplicates a reminder. If the underlying date changes (or the vehicle is
retired / the claim closed), the stale uncompleted reminder is removed and a
fresh one created. Completed reminders are kept as history.

Called from the main views so reminders are always current, and available as
`manage.py sync_reminders` for scheduled jobs.
"""

from datetime import datetime, time, timedelta

from django.utils import timezone

from claims.models import Claim, Reminder, Vehicle

# How far ahead an insurance renewal generates a reminder.
INSURANCE_HORIZON_DAYS = 60


def _due_at(date):
    return timezone.make_aware(datetime.combine(date, time(9, 0)))


def sync_auto_reminders():
    """Create/refresh automated reminders. Returns (created, removed)."""
    horizon = timezone.localdate() + timedelta(days=INSURANCE_HORIZON_DAYS)
    expected = {}

    vehicles = Vehicle.objects.filter(
        status=Vehicle.Status.ACTIVE,
        insurance_due__isnull=False,
        insurance_due__lte=horizon,
    )
    for vehicle in vehicles:
        key = f"veh-ins-{vehicle.pk}-{vehicle.insurance_due.isoformat()}"
        expected[key] = {
            "claim": None,
            "title": f"Insurance renewal — {vehicle.registration}",
            "notes": (
                f"{vehicle.registration} {vehicle.make_model}".strip()
                + f" insurance is due on {vehicle.insurance_due:%d %b %Y}."
            ),
            "due": vehicle.insurance_due,
        }

    open_statuses = [
        Claim.Status.OPEN,
        Claim.Status.AWAITING_SURVEY,
        Claim.Status.AWAITING_INSURER,
    ]
    chase_claims = Claim.objects.filter(
        status__in=open_statuses, chase_on__isnull=False
    )
    for claim in chase_claims:
        key = f"claim-chase-{claim.pk}-{claim.chase_on.isoformat()}"
        title = f"Chase {claim.reference}"
        if claim.next_action:
            title += f": {claim.next_action}"
        expected[key] = {
            "claim": claim,
            "title": title[:200],
            "notes": "",
            "due": claim.chase_on,
        }

    existing = set(
        Reminder.objects.filter(is_auto=True).values_list("auto_key", flat=True)
    )
    created = 0
    for key, spec in expected.items():
        if key not in existing:
            Reminder.objects.create(
                claim=spec["claim"],
                title=spec["title"],
                notes=spec["notes"],
                due_at=_due_at(spec["due"]),
                is_auto=True,
                auto_key=key,
            )
            created += 1

    stale = Reminder.objects.filter(
        is_auto=True, completed_at__isnull=True
    ).exclude(auto_key__in=expected.keys())
    removed = stale.count()
    stale.delete()
    return created, removed
