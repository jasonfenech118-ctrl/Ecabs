"""Role helpers.

A "Garage" user (e.g. the ACR Garage owner, Mario) is limited to the ACR
Garage page only — they can add/edit their own worklist rows and totals, but
see nothing else in the system. Admins/superusers (Francis) keep full access
and see the same rows, so they can help amend what the garage user enters.
"""

GARAGE_GROUP = "Garage"

# URL names a garage-only user is allowed to reach. Everything else redirects
# them back to their worklist.
GARAGE_ALLOWED_URL_NAMES = {
    "garage_jobs",
    "garage_job_add",
    "garage_job_new",
    "garage_job_form",
    "garage_job_update",
    "garage_job_to_invoice",
    "repair_types_manage",
    "garage_metrics",
    "garage_invoices",
    "garage_invoice_new",
    "garage_invoice_edit",
    "garage_invoice_line_add",
    "garage_invoice_line_update",
    "garage_invoice_pdf",
    "stop_impersonate",
    "password_change",
    "password_change_done",
    "logout",
    "login",
}


def is_garage_user(user):
    """True for a signed-in user restricted to the garage section.

    Superusers and staff (admins) are never restricted, even if they are also
    put in the group, so an owner can always see the whole system."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or user.is_staff:
        return False
    return user.groups.filter(name=GARAGE_GROUP).exists()
