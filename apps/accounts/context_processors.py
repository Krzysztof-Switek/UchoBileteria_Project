from django.conf import settings
from django.urls import reverse

from . import dev_credentials


def club_identity(request):
    """Expose club identity/contact settings to every template (KUP-04)."""
    return {
        "club_name": settings.CLUB_NAME,
        "club_contact_email": settings.CLUB_CONTACT_EMAIL,
        "club_address": settings.CLUB_ADDRESS,
        "club_phone": settings.CLUB_PHONE,
    }


def dev_login_accounts(request):
    """One-click demo logins on the two login pages (DEBUG + DEMO mode only)."""
    if request.path in (reverse("login"), reverse("admin:login")):
        return {"dev_accounts": dev_credentials.login_accounts()}
    return {}


def nav_home(request):
    """Where "Start" (and the path root) leads: the admin dashboard for staff,
    the gate screen for door staff — the only area they can use."""
    user = getattr(request, "user", None)
    is_staff = bool(user and user.is_authenticated and user.is_staff)
    if is_staff:
        return {"nav_home_url": reverse("admin:index"), "nav_home_label": "Start",
                "nav_is_staff": True}
    return {"nav_home_url": reverse("checkin:event_select"), "nav_home_label": "Bramka",
            "nav_is_staff": False}
