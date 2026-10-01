"""Local demo accounts (dev_credentials.json) — only active with DEBUG + DEMO mode."""

import json

import pytest
from django.core.management import CommandError, call_command

from apps.accounts import dev_credentials
from apps.orders.models import PaymentConfig, PaymentMode

pytestmark = pytest.mark.django_db


@pytest.fixture
def dev_mode(settings, tmp_path):
    settings.DEBUG = True
    settings.DEV_CREDENTIALS_FILE = tmp_path / "dev_credentials.json"
    return settings.DEV_CREDENTIALS_FILE


def set_live():
    cfg = PaymentConfig.load()
    cfg.mode = PaymentMode.LIVE
    cfg.save()


class TestLoadDevUsersCommand:
    def test_refuses_without_debug(self, settings, tmp_path):
        settings.DEBUG = False
        settings.DEV_CREDENTIALS_FILE = tmp_path / "dev_credentials.json"
        with pytest.raises(CommandError):
            call_command("load_dev_users")
        assert not settings.DEV_CREDENTIALS_FILE.exists()

    def test_refuses_in_live_mode(self, dev_mode):
        set_live()
        with pytest.raises(CommandError):
            call_command("load_dev_users")

    def test_first_run_writes_file_and_creates_accounts(self, dev_mode, client):
        call_command("load_dev_users")
        accounts = json.loads(dev_mode.read_text(encoding="utf-8"))["accounts"]
        assert {a["role"] for a in accounts} == {None, "ADMIN", "MANAGER", "DOOR_STAFF"}
        for account in accounts:
            assert client.login(username=account["username"], password=account["password"])

    def test_door_staff_is_not_staff(self, dev_mode, django_user_model):
        call_command("load_dev_users")
        door = django_user_model.objects.get(username="door-staff@demo.local")
        assert not door.is_staff
        assert list(door.groups.values_list("name", flat=True)) == ["DOOR_STAFF"]

    def test_stale_roles_in_file_rejected(self, dev_mode):
        dev_credentials.write_accounts([{"username": "x@demo.local", "password": "p",
                                         "role": "READ_ONLY"}])
        with pytest.raises(CommandError, match="READ_ONLY"):
            call_command("load_dev_users")

    def test_rerun_keeps_passwords_from_file(self, dev_mode):
        call_command("load_dev_users")
        before = dev_mode.read_text(encoding="utf-8")
        call_command("load_dev_users")
        assert dev_mode.read_text(encoding="utf-8") == before

    def test_existing_user_password_reset_email_kept(self, dev_mode, django_user_model, client):
        django_user_model.objects.create_superuser("admin", "szef@klub.pl", "zapomniane")
        call_command("load_dev_users")
        admin = django_user_model.objects.get(username="admin")
        assert admin.email == "szef@klub.pl"
        password = next(a["password"] for a in dev_credentials.read_accounts()
                        if a["username"] == "admin")
        assert client.login(username="admin", password=password)


class TestLoginPageButtons:
    def test_buttons_on_door_login_page(self, dev_mode, client):
        call_command("load_dev_users")
        response = client.get("/logowanie/")
        assert b"dev-login-accounts" in response.content
        assert b"door-staff@demo.local" in response.content

    def test_admin_login_offers_door_account_via_door_login(self, dev_mode, client):
        """All three roles are offered on /admin/login/ too; the door account
        (no admin access) posts to /logowanie/ instead."""
        call_command("load_dev_users")
        html = client.get("/admin/login/").content.decode()
        assert "dev-login-accounts" in html
        assert 'data-login="door-staff@demo.local"' in html
        assert 'data-login-url="/logowanie/"' in html
        assert 'data-login="manager@demo.local"' in html

    def test_no_buttons_without_debug(self, dev_mode, settings, client):
        call_command("load_dev_users")
        settings.DEBUG = False
        assert b"dev-login-accounts" not in client.get("/logowanie/").content

    def test_no_buttons_in_live_mode(self, dev_mode, client):
        call_command("load_dev_users")
        set_live()
        assert b"dev-login-accounts" not in client.get("/logowanie/").content

    def test_no_buttons_on_other_pages(self, dev_mode, client):
        call_command("load_dev_users")
        assert b"dev-login-accounts" not in client.get("/").content
