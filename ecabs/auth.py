"""Auth view tweaks.

SafePasswordResetView lets the "forgot password" flow degrade gracefully: on
a host with no working email (e.g. the free PythonAnywhere plan, which blocks
outbound SMTP) sending the reset email raises. Rather than show a 500, we still
land on the neutral "check your email" page — the reset page already tells the
user to ask an administrator if no email arrives.
"""

import logging

from django.contrib.auth.views import PasswordResetView
from django.shortcuts import redirect

logger = logging.getLogger(__name__)


class SafePasswordResetView(PasswordResetView):
    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except Exception:
            logger.warning("Password reset email could not be sent", exc_info=True)
            return redirect(self.get_success_url())
