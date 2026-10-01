"""Gate opening window: event day only in LIVE, any date in DEMO."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounts.roles import setup_roles
from apps.checkin.services import gate_status
from apps.events.models import EventStatus
from apps.orders.models import PaymentConfig, PaymentMode
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


def set_mode(mode):
    cfg = PaymentConfig.load()
    cfg.mode = mode
    cfg.save()


def local_dt(days=0, hour=20):
    today = timezone.localdate() + timedelta(days=days)
    return timezone.make_aware(timezone.datetime(today.year, today.month, today.day, hour))


@pytest.fixture
def live():
    set_mode(PaymentMode.LIVE)


@pytest.fixture
def door_client(client, django_user_model):
    setup_roles()
    user = django_user_model.objects.create_user("bramka", password="x")
    user.groups.add(Group.objects.get(name="DOOR_STAFF"))
    client.login(username="bramka", password="x")
    return client


class TestGateStatus:
    def test_demo_opens_any_date(self):
        event = make_event(start_at=local_dt(days=30), end_at=local_dt(days=30, hour=23))
        assert gate_status(event)

    def test_live_closed_before_event_day(self, live):
        event = make_event(start_at=local_dt(days=3), end_at=local_dt(days=3, hour=23))
        gate = gate_status(event)
        assert not gate
        assert "w dniu wydarzenia" in gate.reason

    def test_live_open_from_midnight_of_event_day(self, live):
        event = make_event(start_at=local_dt(hour=20), end_at=local_dt(hour=23))
        assert gate_status(event, now=local_dt(hour=0) + timedelta(minutes=1))

    def test_live_stays_open_past_midnight_until_end(self, live):
        event = make_event(start_at=local_dt(hour=20), end_at=local_dt(days=1, hour=3))
        assert gate_status(event, now=local_dt(days=1, hour=2))
        assert not gate_status(event, now=local_dt(days=1, hour=4))

    def test_live_without_end_uses_fallback(self, live):
        event = make_event(start_at=local_dt(hour=20), end_at=None)
        assert gate_status(event, now=local_dt(days=1, hour=7))
        assert not gate_status(event, now=local_dt(days=1, hour=9))

    def test_unpublished_event_never_opens(self):
        event = make_event(start_at=local_dt(), status=EventStatus.DRAFT)
        assert not gate_status(event)


class TestGateEnforcedInViews:
    def future_event(self):
        event = make_event(start_at=local_dt(days=3), end_at=local_dt(days=3, hour=23))
        make_pool(event)
        return event

    def test_scanner_blocked_before_event_day(self, live, door_client):
        event = self.future_event()
        response = door_client.get(f"/wejscie/{event.id}/skaner/")
        assert response.status_code == 403
        assert "Bramka zamknięta" in response.content.decode()

    def test_scan_post_blocked_before_event_day(self, live, door_client):
        event = self.future_event()
        response = door_client.post(f"/wejscie/{event.id}/skan/", {"code": "ABCD-12"})
        assert response.status_code == 403
        assert response.json()["result"] == "GATE_CLOSED"

    def test_emergency_list_available_before_event_day(self, live, door_client):
        event = self.future_event()
        assert door_client.get(f"/wejscie/{event.id}/lista-awaryjna/").status_code == 200

    def test_event_select_shows_lock_instead_of_gate_button(self, live, door_client):
        event = self.future_event()
        html = door_client.get("/wejscie/").content.decode()
        assert event.title in html
        assert f"/wejscie/{event.id}/skaner/" not in html
        assert "w dniu wydarzenia" in html

    def test_scanner_open_any_date_in_demo(self, door_client):
        event = self.future_event()
        assert door_client.get(f"/wejscie/{event.id}/skaner/").status_code == 200


class TestEventPageTiles:
    def test_event_page_has_gate_lists_and_report_buttons(self, admin_client):
        event = make_event()
        html = admin_client.get(f"/admin/events/event/{event.pk}/change/").content.decode()
        assert "Otwórz bramkę" in html
        assert f"/wejscie/{event.pk}/lista-awaryjna/" in html
        assert f"/wejscie/{event.pk}/lista-awaryjna.csv" in html
        assert f"/raporty/wydarzenie/{event.pk}/" in html
        # moved by JS into the "max tickets per order" row of the basics fieldset
        assert 'id="ucho-event-actions"' in html
        assert ".field-max_tickets_per_order" in html

    def test_event_page_shows_closed_gate_with_reason_in_live(self, live, admin_client):
        event = make_event(start_at=local_dt(days=3), end_at=local_dt(days=3, hour=23))
        html = admin_client.get(f"/admin/events/event/{event.pk}/change/").content.decode()
        assert "Bramka zamknięta" in html
        assert f"/wejscie/{event.pk}/skaner/" not in html

    def test_add_page_has_no_event_tiles(self, admin_client):
        html = admin_client.get("/admin/events/event/add/").content.decode()
        assert "ucho-event-actions" not in html
