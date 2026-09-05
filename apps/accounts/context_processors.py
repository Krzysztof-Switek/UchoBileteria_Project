from django.conf import settings


def club_identity(request):
    """Expose club identity/contact settings to every template (KUP-04)."""
    return {
        "club_name": settings.CLUB_NAME,
        "club_contact_email": settings.CLUB_CONTACT_EMAIL,
        "club_address": settings.CLUB_ADDRESS,
        "club_phone": settings.CLUB_PHONE,
    }
