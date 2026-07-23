"""Template context available on every page."""

from django.utils import timezone

from .models import Reminder
from .roles import is_garage_user

POPUP_LIMIT = 8


def role_flags(request):
    """Expose the current user's role so the nav can be trimmed."""
    return {"is_garage_user": is_garage_user(getattr(request, "user", None))}


def due_reminders(request):
    """Reminders that are due now or overdue and not yet completed — surfaced
    as a login pop-up. Only runs for signed-in users."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    qs = (
        Reminder.objects.filter(completed_at__isnull=True, due_at__lte=timezone.now())
        .select_related("claim")
        .order_by("due_at")
    )
    count = qs.count()
    if not count:
        return {}
    return {
        "due_reminders": list(qs[:POPUP_LIMIT]),
        "due_reminders_count": count,
        "due_reminders_more": max(0, count - POPUP_LIMIT),
    }
