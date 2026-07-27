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
    today = timezone.localdate()
    horizon = today + timedelta(days=INSURANCE_HORIZON_DAYS)
    expected = {}

    for vehicle in Vehicle.objects.filter(status=Vehicle.Status.ACTIVE):
        pay_date = vehicle.pay_date  # current year's pay date
        if not pay_date or pay_date > horizon:
            continue
        key = f"veh-ins-{vehicle.pk}-{pay_date.isoformat()}"
        expected[key] = {
            "claim": None,
            "title": f"Insurance payment — {vehicle.registration}",
            "notes": (
                f"{vehicle.registration} {vehicle.make_model}".strip()
                + f" insurance payment is due on {pay_date:%d %b %Y}."
            ),
            "due": pay_date,
        }

    open_statuses = [
        Claim.Status.OPEN,
        Claim.Status.AWAITING_SURVEY,
        Claim.Status.AWAITING_INSURER,
    ]

    # First advance every non-draft claim's status (survey+liability -> chasing,
    # fully paid -> closed) so reminders reflect the current state.
    for claim in Claim.objects.exclude(status=Claim.Status.DRAFT):
        claim.run_workflow()

    month_tag = f"{today.year}-{today.month:02d}"
    for claim in Claim.objects.filter(status__in=open_statuses):
        ref = claim.reference

        # Chase-on date reminder (manual/next-action)
        if claim.chase_on:
            key = f"claim-chase-{claim.pk}-{claim.chase_on.isoformat()}"
            title = f"Chase {ref}"
            if claim.next_action:
                title += f": {claim.next_action}"
            expected[key] = {"claim": claim, "title": title[:200], "notes": "",
                             "due": claim.chase_on}

        # Survey booked: reminder the day before; chase a week after if not received
        if claim.survey_booked and claim.survey_date and not claim.survey_in_hand:
            if claim.survey_date >= today:
                key = f"claim-surveyday-{claim.pk}-{claim.survey_date.isoformat()}"
                expected[key] = {
                    "claim": claim,
                    "title": f"Survey tomorrow — {ref}",
                    "notes": f"Survey booked for {claim.survey_date:%d %b %Y}.",
                    "due": claim.survey_date - timedelta(days=1),
                }
            else:
                key = f"claim-surveychase-{claim.pk}-{claim.survey_date.isoformat()}"
                expected[key] = {
                    "claim": claim,
                    "title": f"Chase survey report — {ref}",
                    "notes": f"Survey was on {claim.survey_date:%d %b %Y}; report still awaited.",
                    "due": claim.survey_date + timedelta(days=7),
                }

        # Liability disputed (No): monthly chase (or a manually set date)
        if claim.liability == Claim.Liability.DISPUTED:
            due = claim.liability_chase_date or today
            key = f"claim-liability-{claim.pk}-{month_tag}"
            expected[key] = {
                "claim": claim,
                "title": f"Chase liability — {ref}",
                "notes": f"Liability disputed by {claim.third_party_insurer}. {claim.insurer_contact}".strip(),
                "due": due,
            }

        # Payment outstanding after billing: monthly chase to the insurer
        if (
            claim.status == Claim.Status.AWAITING_INSURER
            and claim.bills_sent_on
            and claim.outstanding_amount > 0
        ):
            key = f"claim-paychase-{claim.pk}-{month_tag}"
            whom = claim.insurer_contact or claim.third_party_insurer or "insurer"
            expected[key] = {
                "claim": claim,
                "title": f"Chase payment — {ref}",
                "notes": (
                    f"€{claim.outstanding_amount} outstanding. Chase {whom}."
                ),
                "due": today,
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

    # Remove auto reminders that are no longer expected — but keep overdue,
    # uncompleted ones so an unactioned chase never silently disappears.
    stale = Reminder.objects.filter(
        is_auto=True, completed_at__isnull=True, due_at__gte=timezone.now()
    ).exclude(auto_key__in=expected.keys())
    removed = stale.count()
    stale.delete()
    return created, removed
