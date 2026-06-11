"""
Role definitions (spec section 21) mapped to Django groups + permissions.

Operational notes:
- members of ADMIN / EVENT_MANAGER / SALES_MANAGER / READ_ONLY also need the
  `is_staff` flag to open the Django admin panel,
- DOOR_STAFF accounts must NOT have `is_staff` — they only use /wejscie/.
"""

from django.contrib.auth.models import Group, Permission

ROLE_PERMISSIONS: dict[str, list[tuple[str, str]] | str] = {
    "ADMIN": "__all__",
    "EVENT_MANAGER": [
        ("events", "add_event"),
        ("events", "change_event"),
        ("events", "view_event"),
        ("events", "add_ticketpool"),
        ("events", "change_ticketpool"),
        ("events", "delete_ticketpool"),
        ("events", "view_ticketpool"),
    ],
    "SALES_MANAGER": [
        ("orders", "view_order"),
        ("orders", "change_order"),  # needed for the refund admin action
        ("orders", "view_paymentevent"),
        ("tickets", "view_ticket"),
        ("tickets", "view_emailoutbox"),
        ("events", "view_event"),
        ("events", "view_ticketpool"),
    ],
    "DOOR_STAFF": [
        ("tickets", "checkin_ticket"),
    ],
    "READ_ONLY": [
        ("events", "view_event"),
        ("events", "view_ticketpool"),
        ("orders", "view_order"),
        ("orders", "view_paymentevent"),
        ("tickets", "view_ticket"),
        ("auditlog", "view_auditlog"),
    ],
}

OUR_APPS = ["events", "orders", "tickets", "auditlog"]


def setup_roles() -> list[str]:
    """Create/refresh the role groups. Idempotent."""
    created = []
    for role, spec in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)
        if spec == "__all__":
            perms = Permission.objects.filter(content_type__app_label__in=OUR_APPS)
        else:
            perms = Permission.objects.none()
            ids = []
            for app_label, codename in spec:
                perm = Permission.objects.get(
                    content_type__app_label=app_label, codename=codename
                )
                ids.append(perm.id)
            perms = Permission.objects.filter(id__in=ids)
        group.permissions.set(perms)
        created.append(role)
    return created
