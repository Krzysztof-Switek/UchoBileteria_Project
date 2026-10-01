"""Local demo accounts kept in a gitignored JSON file (dev convenience only).

`manage.py load_dev_users` writes `dev_credentials.json` (one account per role
plus a superuser, random passwords) and syncs those accounts into the database.
The login pages then show one-click "log in as…" buttons for them.

Everything here is a no-op unless DEBUG is on AND payments are in DEMO mode —
production runs with DEBUG=False and the file never leaves the dev machine.
"""

import json
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.urls import reverse

from apps.accounts.roles import ROLE_PERMISSIONS, setup_roles

ROLE_LABELS = {
    "ADMIN": "Administrator (rola)",
    "MANAGER": "Menedżer (wszystko poza DEMO/LIVE)",
    "DOOR_STAFF": "Bramka (skaner)",
}


def credentials_path() -> Path:
    default = settings.BASE_DIR / "dev_credentials.json"
    return Path(getattr(settings, "DEV_CREDENTIALS_FILE", default))


def enabled() -> bool:
    # Imported lazily: apps.orders depends on apps.accounts at import time.
    from apps.orders.models import PaymentConfig

    return bool(settings.DEBUG) and PaymentConfig.is_demo_mode()


def default_accounts() -> list[dict]:
    accounts = [{
        "username": "admin",
        # Not admin@demo.local — that's the ADMIN role account below, and the auth
        # backend can't log in by an e-mail shared by two users.
        "email": "superuser@demo.local",
        "password": secrets.token_urlsafe(12),
        "role": None,
        "superuser": True,
        "label": "Superużytkownik",
    }]
    for role, label in ROLE_LABELS.items():
        email = f"{role.lower().replace('_', '-')}@demo.local"
        accounts.append({
            "username": email,
            "email": email,
            "password": secrets.token_urlsafe(12),
            "role": role,
            "superuser": False,
            "label": label,
        })
    return accounts


def read_accounts() -> list[dict]:
    """Accounts from the JSON file, or [] when missing/invalid."""
    try:
        data = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    return [a for a in accounts if isinstance(a, dict) and a.get("username") and a.get("password")]


def write_accounts(accounts: list[dict]) -> None:
    payload = {
        "_info": "Lokalne konta demo (gitignored). Odśwież: manage.py load_dev_users. "
                 "Działa tylko przy DEBUG=True i trybie płatności DEMO.",
        "accounts": accounts,
    }
    credentials_path().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def unknown_roles(accounts: list[dict]) -> set[str]:
    """Roles in the file that no longer exist (e.g. after the 5 → 3 role change)."""
    return {a["role"] for a in accounts if a.get("role") and a["role"] not in ROLE_PERMISSIONS}


def is_staff_account(account: dict) -> bool:
    # DOOR_STAFF never gets is_staff — same rule as create_staff_user.
    return bool(account.get("superuser")) or account.get("role") != "DOOR_STAFF"


def sync_accounts(accounts: list[dict]) -> list[tuple[dict, bool]]:
    """Create or update each account; returns [(account, created), ...].

    Existing users keep their e-mail; password, staff/superuser flags and role
    group are reset to what the file says.
    """
    setup_roles()
    UserModel = get_user_model()
    result = []
    for account in accounts:
        user, created = UserModel.objects.get_or_create(
            username=account["username"], defaults={"email": account.get("email", "")}
        )
        user.set_password(account["password"])
        user.is_staff = is_staff_account(account)
        user.is_superuser = bool(account.get("superuser"))
        user.is_active = True
        user.save()
        user.groups.clear()
        if account.get("role"):
            user.groups.add(Group.objects.get(name=account["role"]))
        result.append((account, created))
    return result


def login_accounts() -> list[dict]:
    """Accounts to offer on the login pages (empty unless dev demo mode).

    Non-staff accounts (DOOR_STAFF) can't log in through /admin/login/, so their
    button posts to the door login page instead (`login_url`), which sends them
    straight to the scanner — the same buttons work on both pages.
    """
    if not enabled():
        return []
    door_login_url = reverse("login")
    # Username, not e-mail: an existing user keeps its own e-mail (see sync_accounts),
    # and the auth backend accepts either.
    return [{"login": a["username"], "password": a["password"],
             "label": a.get("label") or a.get("role") or a["username"],
             "login_url": "" if is_staff_account(a) else door_login_url}
            for a in read_accounts()]
