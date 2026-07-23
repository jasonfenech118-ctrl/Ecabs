"""Access middleware for role-limited users."""

from django.contrib.auth import get_user_model
from django.shortcuts import redirect
from django.urls import resolve

from .roles import GARAGE_ALLOWED_URL_NAMES, is_garage_user

IMPERSONATE_KEY = "impersonate_id"


class ImpersonationMiddleware:
    """Let an admin (Francis) view the app exactly as another user (Mario).

    When an admin has set the impersonate session key, swap request.user to the
    target user for the request. The real admin is kept on request.impersonator
    so a banner can offer to switch back. Only staff/superusers may impersonate,
    and it can only reduce privilege (to a garage user), never raise it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        target_id = request.session.get(IMPERSONATE_KEY)
        if target_id and user is not None and (user.is_staff or user.is_superuser):
            target = get_user_model().objects.filter(pk=target_id).first()
            # Never impersonate another admin — only lower-privilege users.
            if target and not (target.is_staff or target.is_superuser):
                request.impersonator = user
                request.user = target
        return self.get_response(request)


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
