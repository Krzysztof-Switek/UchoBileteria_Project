"""
Role definitions mapped to Django groups + permissions.

Three roles (decided 2026-10-01, replacing the spec's five):
- ADMIN — everything,
- MANAGER — everything except the payment mode switch (DEMO/LIVE, superuser only): sees all
  data and reports, runs events and pools, refunds, uses the door scanner.
  The role is meant to be operated by an AI agent eventually, so the one
  irreversible "turn on real money" switch stays with a human superuser,
- DOOR_STAFF — the door scanner only.

Operational notes:
- ADMIN and MANAGER need the `is_staff` flag to open the Django admin panel,
- DOOR_STAFF accounts must NOT have `is_staff` — they only use /wejscie/.
"""

from django.contrib.auth.models import Group, Permission

ROLE_PERMISSIONS: dict[str, list[tuple[str, str]] | str] = {
    "ADMIN": "__all__",
    "MANAGER": "__all_but_payment_mode__",
    "DOOR_STAFF": [
        ("tickets", "checkin_ticket"),
    ],
}

# Groups from the earlier five-role model. setup_roles() moves EVENT_MANAGER
# members into MANAGER (same job, plus the scanner) and drops the rest — their
# members lose that group and need a new role assigned by an admin.
RETIRED_ROLES = {"EVENT_MANAGER": "MANAGER", "SALES_MANAGER": None, "READ_ONLY": None}

OUR_APPS = ["events", "orders", "tickets", "auditlog"]

# Withheld from MANAGER: PaymentConfig flips DEMO -> LIVE (real money). The
# admin also limits it to superusers outright (PaymentConfigAdmin); the active
# mode stays visible to everyone as the DEMO/LIVE badge in the nav.
PAYMENT_MODE_PERMISSIONS = [
    "add_paymentconfig", "change_paymentconfig", "delete_paymentconfig", "view_paymentconfig",
]


def setup_roles() -> list[str]:
    """Create/refresh the role groups and retire the old ones. Idempotent."""
    created = []
    for role, spec in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)
        if spec == "__all__":
            perms = Permission.objects.filter(content_type__app_label__in=OUR_APPS)
        elif spec == "__all_but_payment_mode__":
            perms = Permission.objects.filter(
                content_type__app_label__in=OUR_APPS
            ).exclude(codename__in=PAYMENT_MODE_PERMISSIONS)
        else:
            ids = []
            for app_label, codename in spec:
                perm = Permission.objects.get(
                    content_type__app_label=app_label, codename=codename
                )
                ids.append(perm.id)
            perms = Permission.objects.filter(id__in=ids)
        group.permissions.set(perms)
        created.append(role)

    for old_name, new_name in RETIRED_ROLES.items():
        old = Group.objects.filter(name=old_name).first()
        if old is None:
            continue
        if new_name:
            Group.objects.get(name=new_name).user_set.add(*old.user_set.all())
        old.delete()
    return created
