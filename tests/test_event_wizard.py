"""Adding an event: seating chosen in step 1, times past midnight (05.10.2026)."""

import json
from pathlib import Path

import pytest
from django.conf import settings
from django.utils import timezone

from apps.events.models import Event
from apps.seating import services
from apps.seating.hall_import import convert_hall
from apps.seating.models import Room, SeatingLayout
from tests.test_seating import small_layout

pytestmark = pytest.mark.django_db
URL = "/admin/events/event/add/"


def post_data(**over):
    data = {
        "title": "Nocny koncert",
        "seating_choice": "GENERAL",
        "seating_layout": "",
        "max_tickets_per_order": "6",
        "description": "",
        "gates_open_at_0": "24.10.2030", "gates_open_at_1": "22:00",
        "start_at_0": "24.10.2030", "start_at_1": "23:00",
        "end_at_0": "24.10.2030", "end_at_1": "03:00",
        "pools-TOTAL_FORMS": "1", "pools-INITIAL_FORMS": "0",
        "pools-MIN_NUM_FORMS": "0", "pools-MAX_NUM_FORMS": "1000",
        "pools-0-name": "Regular", "pools-0-price_gross": "90", "pools-0-capacity": "50",
        "pools-0-on_sellout": "ACTIVATE_NEXT",
        "pools-0-sales_start_at_0": "01.10.2030", "pools-0-sales_start_at_1": "10:00",
        "pools-0-sales_end_at_0": "24.10.2030", "pools-0-sales_end_at_1": "00:00",
        "_save": "Zapisz",
    }
    data.update(over)
    return data


def test_end_before_start_on_one_day_means_the_night_after(admin_client):
    response = admin_client.post(URL, post_data())
    assert response.status_code == 302, response.content.decode()[:2000]
    event = Event.objects.get(title="Nocny koncert")
    assert timezone.localtime(event.end_at).strftime("%d.%m %H:%M") == "25.10 03:00"


def test_gates_after_start_is_shown_on_the_field(admin_client):
    html = admin_client.post(URL, post_data(gates_open_at_1="23:30")).content.decode()
    assert "muszą otworzyć się przed startem koncertu" in html
    assert not Event.objects.filter(title="Nocny koncert").exists()


def test_seated_event_from_step_one(admin_client):
    room_data = convert_hall(json.loads(
        (Path(settings.BASE_DIR) / "sala_glowna" / "sala_glowna.json").read_text("utf-8")))
    room = Room.objects.create(slug="sala", **room_data)
    layout = SeatingLayout(room=room, name="Test")
    services.save_layout(layout, small_layout())
    response = admin_client.post(URL, post_data(seating_choice="SEATED",
                                                seating_layout=str(layout.pk)))
    assert response.status_code == 302
    event = Event.objects.get(title="Nocny koncert")
    assert event.is_seated and event.capacity_total == 18 and event.seats.count() == 18


def test_seated_without_layout_is_an_error(admin_client):
    html = admin_client.post(URL, post_data(seating_choice="SEATED")).content.decode()
    assert "Wybierz układ sali" in html
