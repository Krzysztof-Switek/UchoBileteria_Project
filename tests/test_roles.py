import pytest
from django.contrib.auth.models import Group

from apps.accounts.roles import ROLE_PERMISSIONS, setup_roles
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


def user_in_role(django_user_model, role, username=None, is_staff=False):
    setup_roles()
    user = django_user_model.objects.create_user(
        username or role.lower(), password="x", is_staff=is_staff
    )
    user.groups.add(Group.objects.get(name=role))
    return user


class TestSetupRoles:
    def test_creates_all_groups(self):
        setup_roles()
        assert set(Group.objects.values_list("name", flat=True)) == set(ROLE_PERMISSIONS)

    def test_idempotent(self):
        setup_roles()
        setup_roles()
        assert Group.objects.count() == len(ROLE_PERMISSIONS)

    def test_admin_group_has_all_app_permissions(self):
        setup_roles()
        admin = Group.objects.get(name="ADMIN")
        assert admin.permissions.filter(codename="checkin_ticket").exists()
        assert admin.permissions.filter(codename="change_order").exists()

    def test_door_staff_has_only_checkin_permission(self):
        setup_roles()
        door = Group.objects.get(name="DOOR_STAFF")
        codenames = set(door.permissions.values_list("codename", flat=True))
        assert codenames == {"checkin_ticket"}


class TestDoorStaffAccess:
    def test_door_staff_can_use_scanner(self, client, django_user_model):
        user_in_role(django_user_model, "DOOR_STAFF")
        client.login(username="door_staff", password="x")
        event = make_event()
        make_pool(event)
        assert client.get("/wejscie/").status_code == 200
        assert client.get(f"/wejscie/{event.id}/skaner/").status_code == 200

    def test_door_staff_cannot_see_reports(self, client, django_user_model):
        user_in_role(django_user_model, "DOOR_STAFF")
        client.login(username="door_staff", password="x")
        response = client.get("/raporty/")
        # no is_staff -> redirected away from the staff-only area
        assert response.status_code == 302

    def test_door_staff_cannot_open_admin(self, client, django_user_model):
        user_in_role(django_user_model, "DOOR_STAFF")
        client.login(username="door_staff", password="x")
        response = client.get("/admin/orders/order/")
        assert response.status_code == 302  # bounced to admin login

    def test_door_staff_login_redirects_to_checkin(self, client, django_user_model):
        user_in_role(django_user_model, "DOOR_STAFF")
        response = client.post(
            "/logowanie/", {"username": "door_staff", "password": "x", "next": ""}
        )
        assert response.status_code == 302
        assert response.url == "/wejscie/"


class TestOtherRolesAccess:
    def test_sales_manager_sees_reports(self, client, django_user_model):
        user_in_role(django_user_model, "SALES_MANAGER", is_staff=True)
        client.login(username="sales_manager", password="x")
        assert client.get("/raporty/").status_code == 200

    def test_read_only_sees_reports_but_cannot_scan(self, client, django_user_model):
        user_in_role(django_user_model, "READ_ONLY", is_staff=True)
        client.login(username="read_only", password="x")
        assert client.get("/raporty/").status_code == 200
        assert client.get("/wejscie/").status_code == 403

    def test_event_manager_cannot_see_reports(self, client, django_user_model):
        user_in_role(django_user_model, "EVENT_MANAGER", is_staff=True)
        client.login(username="event_manager", password="x")
        assert client.get("/raporty/").status_code == 403

    def test_regular_logged_in_user_cannot_scan(self, client, django_user_model):
        django_user_model.objects.create_user("nikt", password="x")
        client.login(username="nikt", password="x")
        assert client.get("/wejscie/").status_code == 403

    def test_admin_role_can_do_everything(self, client, django_user_model):
        user_in_role(django_user_model, "ADMIN", is_staff=True)
        client.login(username="admin", password="x")
        assert client.get("/raporty/").status_code == 200
        assert client.get("/wejscie/").status_code == 200
