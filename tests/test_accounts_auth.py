from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.core.management import CommandError, call_command

from apps.accounts.forms import EmailAdminAuthenticationForm
from apps.accounts.ratelimit import MAX_FAILURES
from apps.accounts.roles import setup_roles

pytestmark = pytest.mark.django_db


def make_staff(django_user_model, *, email="szef@klub.pl", username="szef", password="x"):
    setup_roles()
    user = django_user_model.objects.create_user(
        username, email=email, password=password, is_staff=True
    )
    user.groups.add(Group.objects.get(name="ADMIN"))
    return user


class TestEmailLogin:
    def test_login_with_email(self, client, django_user_model):
        make_staff(django_user_model)
        assert client.login(username="szef@klub.pl", password="x")

    def test_login_with_email_case_insensitive(self, client, django_user_model):
        make_staff(django_user_model)
        assert client.login(username="SZEF@Klub.pl", password="x")

    def test_login_with_username_still_works(self, client, django_user_model):
        """Backward compatible: accounts not (yet) keyed by e-mail still log in."""
        make_staff(django_user_model)
        assert client.login(username="szef", password="x")

    def test_wrong_password_rejected(self, client, django_user_model):
        make_staff(django_user_model)
        assert not client.login(username="szef@klub.pl", password="wrong")

    def test_unknown_identifier_rejected(self, client, django_user_model):
        assert not client.login(username="nikt@nigdzie.pl", password="x")

    def test_admin_login_form_labelled_for_email(self):
        form = EmailAdminAuthenticationForm()
        assert form.fields["username"].label == "Adres e-mail"


class TestLoginRateLimit:
    """Uses real POSTs to /logowanie/ rather than client.login(), because the
    Django test client's login() shortcut calls authenticate() without a
    request object — and the throttle only applies when a request is present
    (matching real login forms, which always pass one). Also needs a real
    cache backend: settings_test.py defaults CACHES to DummyCache so rate
    limiting is opt-in per test (see test_orders.py for the same pattern)."""

    def _attempt(self, client, password):
        return client.post(
            "/logowanie/", {"username": "szef@klub.pl", "password": password, "next": ""}
        )

    def test_locks_out_after_repeated_failures(self, client, django_user_model, settings):
        settings.CACHES = {
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "login-ratelimit-test-1",
            }
        }
        make_staff(django_user_model)
        for _ in range(MAX_FAILURES):
            assert self._attempt(client, "wrong").status_code == 200  # form re-rendered
        # Correct password now also rejected: the identifier itself is throttled.
        assert self._attempt(client, "x").status_code == 200

    def test_successful_login_clears_the_counter(self, client, django_user_model, settings):
        settings.CACHES = {
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "login-ratelimit-test-2",
            }
        }
        make_staff(django_user_model)
        for _ in range(MAX_FAILURES - 1):
            assert self._attempt(client, "wrong").status_code == 200
        assert self._attempt(client, "x").status_code == 302  # succeeds, clears counter

        client.post("/wyloguj/")
        for _ in range(MAX_FAILURES - 1):
            assert self._attempt(client, "wrong").status_code == 200
        # If the earlier success hadn't cleared the counter, this would now be
        # over MAX_FAILURES and get throttled instead of succeeding.
        assert self._attempt(client, "x").status_code == 302


class TestCreateStaffUserCommand:
    def test_creates_admin_account(self, django_user_model):
        setup_roles()
        with patch("getpass.getpass", side_effect=["StrongPassphrase123", "StrongPassphrase123"]):
            call_command("create_staff_user", "nowy@klub.pl", "ADMIN")

        user = django_user_model.objects.get(email="nowy@klub.pl")
        assert user.username == "nowy@klub.pl"
        assert user.is_staff is True
        assert user.groups.filter(name="ADMIN").exists()
        assert user.check_password("StrongPassphrase123")

    def test_door_staff_does_not_get_is_staff(self, django_user_model):
        setup_roles()
        with patch("getpass.getpass", side_effect=["StrongPassphrase123", "StrongPassphrase123"]):
            call_command("create_staff_user", "brama@klub.pl", "DOOR_STAFF")

        user = django_user_model.objects.get(email="brama@klub.pl")
        assert user.is_staff is False

    def test_rejects_weak_password(self, django_user_model):
        setup_roles()
        with patch("getpass.getpass", side_effect=["short", "short"]):
            with pytest.raises(CommandError):
                call_command("create_staff_user", "slaby@klub.pl", "ADMIN")
        assert not django_user_model.objects.filter(email="slaby@klub.pl").exists()

    def test_rejects_mismatched_passwords(self, django_user_model):
        setup_roles()
        with patch("getpass.getpass", side_effect=["StrongPassphrase123", "Inne haslo 456"]):
            with pytest.raises(CommandError):
                call_command("create_staff_user", "rozne@klub.pl", "ADMIN")
        assert not django_user_model.objects.filter(email="rozne@klub.pl").exists()

    def test_rejects_duplicate_email(self, django_user_model):
        setup_roles()
        make_staff(django_user_model, email="powtorka@klub.pl", username="powtorka")
        with pytest.raises(CommandError):
            call_command("create_staff_user", "powtorka@klub.pl", "ADMIN")

    def test_requires_setup_roles_first(self, django_user_model):
        with patch("getpass.getpass", side_effect=["StrongPassphrase123", "StrongPassphrase123"]):
            with pytest.raises(CommandError):
                call_command("create_staff_user", "brak-grupy@klub.pl", "ADMIN")
