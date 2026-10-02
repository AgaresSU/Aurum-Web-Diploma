from django.shortcuts import redirect
from django.urls import reverse

from .access import can_use_django_admin, is_admin_user, needs_totp_setup


class RequireStaffTotpMiddleware:
    """Keep staff/admin users inside the TOTP setup flow until 2FA is enabled."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path_info
        is_admin = path == "/admin" or path.startswith("/admin/")

        if user and user.is_authenticated and needs_totp_setup(user):
            security_path = reverse("office:security")
            is_office = path == "/office" or path.startswith("/office/")
            if (is_office or is_admin) and path != security_path:
                return redirect(security_path)

        if user and user.is_authenticated and is_admin and is_admin_user(user) and not can_use_django_admin(user):
            return redirect("office:dashboard")

        return self.get_response(request)
