import pytest
from django.contrib.auth.models import Group

from apps.accounts.roles import ROLE_PERMISSIONS, setup_roles
from tests.factories import make_event, make_order, make_pool

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


class TestManagerAccess:
    """MANAGER = everything except switching DEMO/LIVE (to be run by an AI agent)."""

    def login(self, client, django_user_model):
        user_in_role(django_user_model, "MANAGER", is_staff=True)
        client.login(username="manager", password="x")

    def test_manager_can_scan(self, client, django_user_model):
        self.login(client, django_user_model)
        event = make_event()
        make_pool(event)
        assert client.get("/wejscie/").status_code == 200
        assert client.get(f"/wejscie/{event.id}/skaner/").status_code == 200

    def test_manager_sees_everything(self, client, django_user_model):
        self.login(client, django_user_model)
        for url in ("/admin/events/event/add/", "/admin/orders/order/wydarzenia/",
                    "/admin/orders/order/?wszystkie=1", "/admin/tickets/ticket/?wszystkie=1",
                    "/admin/orders/paymentevent/?wszystkie=1",
                    "/admin/auditlog/auditlog/?wszystkie=1",
                    "/raporty/", "/szukaj/?q=abc"):
            assert client.get(url).status_code == 200, url

    def test_manager_dashboard_shows_events_tile(self, client, django_user_model):
        make_pool(make_event(title="Koncert na pulpicie"))
        self.login(client, django_user_model)
        assert "Koncert na pulpicie" in client.get("/admin/").content.decode()

    def test_payment_mode_switch_is_superuser_only(self, client, django_user_model):
        """DEMO/LIVE config is hidden from every role: MANAGER and ADMIN group alike."""
        from apps.orders.models import PaymentConfig, PaymentMode

        cfg = PaymentConfig.load()
        self.login(client, django_user_model)
        assert client.get("/admin/orders/paymentconfig/").status_code == 403
        assert "/admin/orders/paymentconfig/" not in client.get("/admin/").content.decode()
        client.post(f"/admin/orders/paymentconfig/{cfg.pk}/change/", {"mode": PaymentMode.LIVE})
        cfg.refresh_from_db()
        assert cfg.mode == PaymentMode.DEMO

    def test_admin_group_cannot_see_payment_mode_either(self, client, django_user_model):
        user_in_role(django_user_model, "ADMIN", is_staff=True)
        client.login(username="admin", password="x")
        assert client.get("/admin/orders/paymentconfig/").status_code == 403


class TestBulkActionPermissions:
    """Admin actions without `permissions=` were offered to view-only users —
    each action now names the permission it needs."""

    def actions(self, client, django_user_model, role, url):
        # Django renders the action <select> only when the changelist has rows.
        event = make_event()
        make_order(event, make_pool(event))
        user_in_role(django_user_model, role, is_staff=True)
        client.login(username=role.lower(), password="x")
        return client.get(url).content.decode()

    def test_manager_has_event_and_refund_actions(self, client, django_user_model):
        html = self.actions(client, django_user_model, "MANAGER", "/admin/events/event/")
        assert 'value="publish_events"' in html
        assert 'value="cancel_events"' in html
        assert 'value="cancel_events_with_refunds"' in html
        orders_html = client.get("/admin/orders/order/?wszystkie=1").content.decode()
        assert 'value="refund_selected_orders"' in orders_html

    def test_manager_can_force_pool_status(self, client, django_user_model):
        html = self.actions(client, django_user_model, "MANAGER", "/admin/events/ticketpool/")
        assert 'value="force_open"' in html

    def test_admin_has_every_action(self, client, django_user_model):
        html = self.actions(client, django_user_model, "ADMIN", "/admin/events/event/")
        assert 'value="cancel_events_with_refunds"' in html
        orders_html = client.get("/admin/orders/order/?wszystkie=1").content.decode()
        assert 'value="refund_selected_orders"' in orders_html

    def test_view_only_user_cannot_run_event_actions(self, client, django_user_model):
        """A staff user who can only view events gets no actions — and a forged
        POST of the refund action is rejected without cancelling anything."""
        from django.contrib.auth.models import Permission

        from apps.events.models import EventStatus

        user = django_user_model.objects.create_user("podglad", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_event"))
        client.login(username="podglad", password="x")
        event = make_event()
        html = client.get("/admin/events/event/").content.decode()
        assert 'value="cancel_events_with_refunds"' not in html
        client.post("/admin/events/event/", {
            "action": "cancel_events_with_refunds", "_selected_action": [event.pk],
        })
        event.refresh_from_db()
        assert event.status != EventStatus.CANCELLED


class TestOtherAccess:
    def test_regular_logged_in_user_cannot_scan(self, client, django_user_model):
        django_user_model.objects.create_user("nikt", password="x")
        client.login(username="nikt", password="x")
        assert client.get("/wejscie/").status_code == 403

    def test_admin_role_can_do_everything(self, client, django_user_model):
        user_in_role(django_user_model, "ADMIN", is_staff=True)
        client.login(username="admin", password="x")
        assert client.get("/raporty/").status_code == 200
        assert client.get("/wejscie/").status_code == 200
        assert client.get("/szukaj/?q=abc").status_code == 200


class TestRetiredRoles:
    def test_old_groups_removed_and_event_managers_migrated(self, django_user_model):
        for name in ("EVENT_MANAGER", "SALES_MANAGER", "READ_ONLY"):
            Group.objects.create(name=name)
        user = django_user_model.objects.create_user("stary", password="x", is_staff=True)
        user.groups.add(Group.objects.get(name="EVENT_MANAGER"))
        setup_roles()
        assert set(Group.objects.values_list("name", flat=True)) == set(ROLE_PERMISSIONS)
        assert list(user.groups.values_list("name", flat=True)) == ["MANAGER"]
