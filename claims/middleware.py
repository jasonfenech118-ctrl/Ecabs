"""Access middleware for role-limited users."""

from django.shortcuts import redirect
from django.urls import resolve

from .roles import GARAGE_ALLOWED_URL_NAMES, is_garage_user


class GarageAccessMiddleware:
    """Keep garage-only users inside the ACR Garage section.

    Any request from a garage user to a page outside the allow-list is bounced
    to their worklist. Admin, static files and auth pages are unaffected."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and is_garage_user(user):
            match = resolve(request.path_info)
            # Block the Django admin and anything not explicitly allowed.
            if (
                match.app_name == "admin"
                or match.url_name not in GARAGE_ALLOWED_URL_NAMES
            ):
                return redirect("garage_jobs")
        return self.get_response(request)
