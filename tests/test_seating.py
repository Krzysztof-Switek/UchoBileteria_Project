"""Seat planner: layout validation, attaching to events, reservation in every
order path (buy, conflict, expiry, decline, abandon, refund, late webhook,
event cancellation), seat changes and staff tools (03.10 plan, etap 1-4)."""

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.roles import setup_roles
from apps.events.models import Event
from apps.orders import services as order_services
from apps.orders.models import OrderStatus
from apps.orders.payments import mark_order_failed, mark_order_paid, mark_order_refunded
from apps.orders.refunds import cancel_event_with_refunds
from apps.seating import services
from apps.seating.hall_import import convert_hall
from apps.seating.layout import (
    LayoutError,
    clean_layout,
    expand_seats,
    seat_positions,
    validate_layout,
)
from apps.seating.models import EventSeat, Room, SeatingLayout, SeatStatus
from apps.tickets.models import Ticket, TicketStatus
from tests.factories import make_event, make_order, make_pool

pytestmark = pytest.mark.django_db

HALL_FILE = Path(settings.BASE_DIR) / "sala_glowna" / "sala_glowna.json"


def seats(obj_id, n):
    return [{"id": f"{obj_id}-s{i}", "label": str(i), "disabled": False} for i in range(1, n + 1)]


def table(oid, label, x, y, n=4, mode="SEATS", r=45):
    return {"id": oid, "type": "table_round", "label": label, "x": x, "y": y, "r": r,
            "rotation": 0, "sell_mode": mode, "category": "", "seats": seats(oid, n)}


def small_layout():
    """t1 (4 seats, singles), t2 (4 seats, TABLE only), t3 (4 seats, BOTH), row A (6)."""
    return {"schema_version": 1, "grid_cm": 10, "objects": [
        table("t1", "1", 300, 700),
        table("t2", "2", 540, 700, mode="TABLE"),
        table("t3", "3", 300, 950, mode="BOTH"),
        {"id": "r1", "type": "row", "label": "A", "x": 440, "y": 1300, "spacing": 52,
         "rotation": 0, "sell_mode": "SEATS", "category": "", "numbering": "ltr", "start": 1,
         "seats": seats("r1", 6)},
    ]}


@pytest.fixture
def room():
    data = convert_hall(json.loads(HALL_FILE.read_text(encoding="utf-8")))
    return Room.objects.create(slug="sala-glowna", **data)


@pytest.fixture
def layout(room):
    lay = SeatingLayout(room=room, name="Test")
    services.save_layout(lay, small_layout())
    return lay


@pytest.fixture
def seated_event(layout):
    event = make_event(status="DRAFT", max_tickets_per_order=4)
    make_pool(event, capacity=100, price_gross=Decimal("50.00"))
    services.attach_layout(event, layout)
    event.status = "PUBLISHED"
    event.save()
    event.refresh_from_db()
    return event


def seat(event, key):
    return EventSeat.objects.get(event=event, seat_key=key)


def buy(event, keys, email="a@example.com"):
    return order_services.create_order(event, email, seat_keys=keys)


# --- hall + layout document ------------------------------------------------------


class TestHallImport:
    def test_converts_metres_to_canonical_centimetres(self, room):
        # plan ends at the outer face of the inner north wall (owner's view, 05.10)
        assert (room.width_cm, room.depth_cm) == (1058, 2230)
        stage = next(o for o in room.geometry["obstacles"] if o["kind"] == "stage")
        # stage = back of the hall (Y 17.7..22.3 m) -> top of the plan (v 0..460 cm)
        vs = [p[1] for p in stage["points"]]
        assert min(vs) == 0 and max(vs) == 460
        assert any(o["kind"] == "column" for o in room.geometry["obstacles"])
        assert room.geometry["entrances"] and room.geometry["pit"]

    def test_owner_view_of_the_hall(self, room):
        g = room.geometry
        assert max(p[0] for p in g["outline"]) == 980  # no corridor behind the north wall
        assert {e["name"] for e in g["entrances"]} == {"Wejście główne", "Wejście na scenę"}
        names = " ".join(c["name"] for c in g["context"])
        assert "DJ" not in names and "Biuro" not in names and "VIP" not in names
        assert "Realizator" not in names
        mezzanine = next(c for c in g["context"] if c["id"] == "VEN_ROOM_ANTRESOLA")
        assert max(p[1] for p in mezzanine["points"]) == 2230  # over the bar to the back wall
        barrier = next(c for c in g["context"] if c["kind"] == "barrier")
        assert [p[0] for p in barrier["points"]] == [0, 980]  # wall to wall
        stage = next(o for o in g["obstacles"] if o["kind"] == "stage")
        assert max(p[0] for p in stage["points"]) == 980

    def test_command_is_idempotent(self):
        from django.core.management import call_command

        call_command("import_hall")
        call_command("import_hall")
        assert Room.objects.count() == 1


class TestLayoutDocument:
    def test_valid_layout_has_no_errors_or_warnings(self, room):
        errors, warnings = validate_layout(small_layout(), room.geometry)
        assert errors == [] and warnings == []

    def test_duplicate_table_number_is_an_error(self):
        data = small_layout()
        data["objects"][1]["label"] = "1"
        errors, _ = validate_layout(data)
        assert any("Zdublowany numer" in e["message"] for e in errors)

    def test_rect_sides_must_match_seat_count(self):
        data = {"objects": [{"id": "t9", "type": "table_rect", "label": "9", "x": 0, "y": 0,
                             "w": 120, "h": 80, "sides": {"top": 2, "bottom": 2},
                             "sell_mode": "SEATS", "seats": seats("t9", 3)}]}
        errors, _ = validate_layout(data)
        assert any("rozkładem na boki" in e["message"] for e in errors)

    def test_rows_only_sell_single_seats(self):
        data = small_layout()
        data["objects"][3]["sell_mode"] = "TABLE"
        errors, _ = validate_layout(data)
        assert any("tylko na pojedyncze" in e["message"] for e in errors)

    def test_layout_needs_a_sellable_seat(self):
        data = {"objects": [table("t1", "1", 300, 700, n=1)]}
        data["objects"][0]["seats"][0]["disabled"] = True
        with pytest.raises(LayoutError):
            clean_layout(data)

    def test_warnings_for_stage_column_and_outside(self, room):
        data = {"objects": [
            table("t1", "1", 500, 200),  # on the stage
            table("t2", "2", 755, 965),  # around a mezzanine column
            table("t3", "3", 1100, 1500),  # behind the inner wall, outside the hall
        ]}
        errors, warnings = validate_layout(data, room.geometry)
        assert errors == []
        text = " ".join(w["message"] for w in warnings)
        assert "Scena" in text and "Słup" in text and "poza obrys" in text

    def test_seats_blocking_an_exit_warn(self, room):
        data = {"objects": [table("t1", "1", 60, 1320, n=2)]}  # in front of the main entrance
        _, warnings = validate_layout(data, room.geometry)
        assert any("zastawia przejście" in w["message"] for w in warnings)

    def test_seat_geometry_round_table_clockwise_from_top(self):
        pos = seat_positions(table("t1", "1", 0, 0, n=4, r=45))
        assert [tuple(round(c) for c in p) for p in pos] == [(0, -75), (75, 0), (0, 75), (-75, 0)]

    def test_disabled_seat_is_not_sold(self):
        data = small_layout()
        data["objects"][0]["seats"][1]["disabled"] = True
        keys = [s["seat_key"] for s in expand_seats(data)]
        assert "t1-s2" not in keys and len(keys) == 17

    def test_clean_layout_drops_unknown_keys(self):
        data = small_layout()
        data["objects"][0]["evil"] = "<script>"
        assert "evil" not in clean_layout(data)["objects"][0]


class TestLayoutLibrary:
    def test_save_counts_seats_and_copy_keeps_original(self, layout):
        assert (layout.seat_count, layout.table_count) == (18, 4)
        data = small_layout()
        data["objects"].pop()
        copy = services.copy_layout(layout, "Kopia", data=data)
        layout.refresh_from_db()
        assert copy.based_on == layout and copy.seat_count == 12 and layout.seat_count == 18


# --- attaching a layout to an event ------------------------------------------------


class TestAttach:
    def test_snapshot_seats_and_capacity(self, seated_event, layout):
        assert seated_event.is_seated
        assert seated_event.capacity_total == 18
        assert seated_event.seats.count() == 18
        # later edits of the layout never touch the event's frozen copy
        data = small_layout()
        data["objects"].pop()
        services.save_layout(layout, data)
        assert seated_event.seats.count() == 18
        assert len(seated_event.seat_map.layout_snapshot["objects"]) == 4

    def test_structure_locked_after_first_sale(self, seated_event, layout):
        buy(seated_event, ["t1-s1"])
        with pytest.raises(ValidationError):
            services.attach_layout(seated_event, layout)
        with pytest.raises(ValidationError):
            services.detach_layout(seated_event)

    def test_detach_back_to_general(self, seated_event):
        services.detach_layout(seated_event)
        seated_event.refresh_from_db()
        assert not seated_event.is_seated and seated_event.seats.count() == 0
        assert seated_event.capacity_total == 100

    def test_publish_requires_seat_map(self):
        from apps.events.services import publish_event

        event = make_event(status="DRAFT", seating_mode="SEATED")
        make_pool(event)
        with pytest.raises(ValidationError):
            publish_event(event)


# --- buying ----------------------------------------------------------------------------


class TestBuying:
    def test_buy_holds_seats_and_counters(self, seated_event):
        order = buy(seated_event, ["t1-s1", "t1-s2"])
        assert order.quantity == 2 and order.amount_gross == Decimal("100.00")
        assert order.seat_labels == ["Stolik 1 · miejsce 1", "Stolik 1 · miejsce 2"]
        assert seat(seated_event, "t1-s1").status == SeatStatus.HELD
        seated_event.refresh_from_db()
        assert seated_event.sold_total == 2

    def test_same_seat_cannot_be_sold_twice(self, seated_event):
        buy(seated_event, ["t1-s1", "t1-s2"])
        with pytest.raises(order_services.SeatConflictError) as exc:
            buy(seated_event, ["t1-s2", "t1-s3"], email="b@example.com")
        assert exc.value.taken_labels == ["Stolik 1 · miejsce 2"]
        assert exc.value.still_free == ["t1-s3"]
        # the losing order rolled back completely: no order, seat still free, counters
        assert seat(seated_event, "t1-s3").status == SeatStatus.AVAILABLE
        seated_event.refresh_from_db()
        assert seated_event.sold_total == 2
        assert seated_event.orders.count() == 1

    def test_table_only_table_must_be_whole(self, seated_event):
        with pytest.raises(order_services.PurchaseError, match="tylko w całości"):
            buy(seated_event, ["t2-s1", "t2-s2"])
        order = buy(seated_event, ["t2-s1", "t2-s2", "t2-s3", "t2-s4"])
        assert order.quantity == 4

    def test_single_seat_limit_but_whole_tables_exempt(self, seated_event):
        # max_tickets_per_order = 4: five single seats are too many...
        with pytest.raises(order_services.PurchaseError, match="najwyżej 4"):
            buy(seated_event, ["r1-s1", "r1-s2", "r1-s3", "r1-s4", "r1-s5"])
        # ...but two whole tables (8 seats) plus one single seat are fine (D3)
        keys = [f"t2-s{i}" for i in range(1, 5)] + [f"t3-s{i}" for i in range(1, 5)] + ["r1-s1"]
        assert buy(seated_event, keys).quantity == 9

    def test_unknown_or_foreign_seat_rejected(self, seated_event):
        with pytest.raises(order_services.PurchaseError):
            buy(seated_event, ["nie-ma"])
        with pytest.raises(order_services.PurchaseError):
            buy(seated_event, ["t1-s1", "t1-s1"])

    def test_blocked_seat_cannot_be_bought(self, seated_event):
        services.block_seats(seated_event, ["t1-s1"], "goście")
        with pytest.raises(order_services.SeatConflictError):
            buy(seated_event, ["t1-s1"])

    def test_pool_limit_still_applies(self, layout):
        event = make_event(status="DRAFT")
        make_pool(event, capacity=2)
        services.attach_layout(event, layout)
        event.status = "PUBLISHED"
        event.save()
        with pytest.raises(order_services.CapacityError):
            buy(event, ["t1-s1", "t1-s2", "t1-s3"])
        assert seat(event, "t1-s1").status == SeatStatus.AVAILABLE


# --- every way out of an order releases its seats ---------------------------------------


class TestReleasing:
    def test_paid_order_sells_seats_and_issues_seated_tickets(self, seated_event):
        order = buy(seated_event, ["t1-s1", "t1-s2"])
        assert mark_order_paid(order)
        assert seat(seated_event, "t1-s1").status == SeatStatus.SOLD
        labels = sorted(t.seat.label for t in order.tickets.all())
        assert labels == ["Stolik 1 · miejsce 1", "Stolik 1 · miejsce 2"]

    @pytest.mark.parametrize("finish", ["expire", "fail", "abandon"])
    def test_unpaid_exits_free_the_seats(self, seated_event, finish):
        order = buy(seated_event, ["t1-s1", "t1-s2"])
        {
            "expire": order_services.expire_order,
            "fail": mark_order_failed,
            "abandon": order_services.cancel_unpaid_order,
        }[finish](order)
        assert seat(seated_event, "t1-s1").status == SeatStatus.AVAILABLE
        assert seat(seated_event, "t1-s1").order is None
        seated_event.refresh_from_db()
        assert seated_event.sold_total == 0
        order.refresh_from_db()
        assert order.seat_labels  # history kept on the order
        buy(seated_event, ["t1-s1"], email="next@example.com")  # sellable again

    def test_expire_stale_orders_cron(self, seated_event):
        buy(seated_event, ["t1-s1"])
        order_services.expire_stale_orders(now=timezone.now() + timedelta(hours=1))
        assert seat(seated_event, "t1-s1").status == SeatStatus.AVAILABLE

    def test_refund_frees_seats_and_keeps_ticket_history(self, seated_event):
        order = buy(seated_event, ["t1-s1"])
        mark_order_paid(order)
        assert mark_order_refunded(order)
        assert seat(seated_event, "t1-s1").status == SeatStatus.AVAILABLE
        ticket = order.tickets.get()
        assert ticket.status == TicketStatus.REFUNDED
        assert ticket.seat.seat_key == "t1-s1"  # who sat there stays visible

    def test_late_webhook_retakes_the_same_free_seats(self, seated_event):
        order = buy(seated_event, ["t1-s1"])
        order_services.expire_order(order)
        assert mark_order_paid(order)
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID
        assert seat(seated_event, "t1-s1").status == SeatStatus.SOLD
        assert order.tickets.get().seat.seat_key == "t1-s1"

    def test_late_webhook_when_seat_was_resold(self, seated_event):
        order = buy(seated_event, ["t1-s1", "t1-s2"])
        order_services.expire_order(order)
        other = buy(seated_event, ["t1-s2"], email="b@example.com")
        assert not mark_order_paid(order)  # stays EXPIRED -> refund flow
        order.refresh_from_db()
        assert order.status == OrderStatus.EXPIRED
        # nothing half-taken: t1-s1 still free, t1-s2 still the other buyer's
        assert seat(seated_event, "t1-s1").status == SeatStatus.AVAILABLE
        assert seat(seated_event, "t1-s2").order == other
        seated_event.refresh_from_db()
        assert seated_event.sold_total == 1

    def test_event_cancellation_refunds_and_frees(self, seated_event):
        paid = buy(seated_event, ["t1-s1"])
        mark_order_paid(paid)
        pending = buy(seated_event, ["t1-s2"], email="b@example.com")
        cancel_event_with_refunds(seated_event)
        assert not EventSeat.objects.filter(
            event=seated_event, status__in=[SeatStatus.HELD, SeatStatus.SOLD]
        ).exists()
        pending.refresh_from_db()
        assert pending.status == OrderStatus.CANCELLED


# --- changes ---------------------------------------------------------------------------------


class TestChanges:
    def test_buyer_swaps_seats_before_paying(self, seated_event):
        order = buy(seated_event, ["t1-s1", "t1-s2"])
        order_services.change_pending_order_seats(order, ["t1-s2", "t1-s3"])
        assert order.seat_keys == ["t1-s2", "t1-s3"]
        assert seat(seated_event, "t1-s1").status == SeatStatus.AVAILABLE
        assert seat(seated_event, "t1-s3").order == order
        seated_event.refresh_from_db()
        assert seated_event.sold_total == 2

    def test_buyer_swap_needs_same_count_and_free_seats(self, seated_event):
        order = buy(seated_event, ["t1-s1"])
        buy(seated_event, ["t1-s4"], email="b@example.com")
        with pytest.raises(order_services.PurchaseError, match="tyle samo"):
            order_services.change_pending_order_seats(order, ["t1-s2", "t1-s3"])
        with pytest.raises(order_services.SeatConflictError):
            order_services.change_pending_order_seats(order, ["t1-s4"])
        order.refresh_from_db()
        assert order.seat_keys == ["t1-s1"]
        assert seat(seated_event, "t1-s1").order == order

    def test_paid_order_cannot_be_swapped_by_buyer(self, seated_event):
        order = buy(seated_event, ["t1-s1"])
        mark_order_paid(order)
        order.refresh_from_db()
        with pytest.raises(order_services.PurchaseError):
            order_services.change_pending_order_seats(order, ["t1-s2"])

    def test_staff_moves_paid_buyer_ticket_follows(self, seated_event):
        from apps.tickets.models import EmailOutbox

        order = buy(seated_event, ["t1-s1", "t1-s2"])
        mark_order_paid(order)
        code = order.tickets.get(seat__seat_key="t1-s1").short_code
        services.move_seat(seated_event, "t1-s1", "t3-s1")
        ticket = Ticket.objects.get(short_code=code)
        assert ticket.seat.seat_key == "t3-s1"  # same ticket, same QR, new seat
        assert seat(seated_event, "t1-s1").status == SeatStatus.AVAILABLE
        assert seat(seated_event, "t3-s1").status == SeatStatus.SOLD
        order.refresh_from_db()
        assert order.seat_labels == ["Stolik 3 · miejsce 1", "Stolik 1 · miejsce 2"]
        email = EmailOutbox.objects.get(subject__startswith="Zmiana miejsca")
        assert "Stolik 3 · miejsce 1" in email.body_text

    def test_staff_move_to_taken_seat_fails(self, seated_event):
        buy(seated_event, ["t1-s1"])
        buy(seated_event, ["t1-s2"], email="b@example.com")
        with pytest.raises(ValidationError):
            services.move_seat(seated_event, "t1-s1", "t1-s2")

    def test_block_and_unblock_adjust_capacity(self, seated_event):
        assert services.block_seats(seated_event, ["t1-s1", "t1-s2"], "technika") == 2
        seated_event.refresh_from_db()
        assert seated_event.capacity_total == 16
        assert seat(seated_event, "t1-s1").block_note == "technika"
        assert services.unblock_seats(seated_event, ["t1-s1"]) == 1
        seated_event.refresh_from_db()
        assert seated_event.capacity_total == 17

    def test_sold_seat_cannot_be_blocked(self, seated_event):
        buy(seated_event, ["t1-s1"])
        assert services.block_seats(seated_event, ["t1-s1"], "x") == 0

    def test_table_only_selection_ignores_blocked_seat(self, seated_event):
        services.block_seats(seated_event, ["t2-s4"], "wózek")
        assert buy(seated_event, ["t2-s1", "t2-s2", "t2-s3"]).quantity == 3


# --- views ------------------------------------------------------------------------------------


class TestBuyerViews:
    def test_event_page_shows_map_config(self, client, seated_event):
        html = client.get(f"/wydarzenia/{seated_event.slug}/").content.decode()
        assert 'id="sm-buy-config"' in html and "seatmap-buy.js" in html
        assert 'name="quantity"' not in html

    def test_states_json_hides_buyers(self, client, seated_event):
        buy(seated_event, ["t1-s1"])
        services.block_seats(seated_event, ["t1-s2"], "x")
        data = client.get(f"/wydarzenia/{seated_event.slug}/miejsca.json").json()
        assert data["states"]["t1-s1"] == "taken"
        assert data["states"]["t1-s2"] == "blocked"
        assert data["states"]["t1-s3"] == "free"
        assert "a@example.com" not in json.dumps(data)

    def test_purchase_redirects_to_checkout(self, client, seated_event):
        response = client.post(f"/kup/{seated_event.slug}/",
                               {
                                   "accept_terms": "on", "buyer_email": "a@example.com",
                                   "seats": "t1-s1,t1-s2",
                               })
        assert response.status_code == 302 and "/kasa-demo/" in response["Location"]
        assert seat(seated_event, "t1-s2").status == SeatStatus.HELD

    def test_conflict_returns_to_map_with_remaining_selection(self, client, seated_event):
        buy(seated_event, ["t1-s1"])
        response = client.post(f"/kup/{seated_event.slug}/",
                               {
                                   "accept_terms": "on", "buyer_email": "b@example.com",
                                   "seats": "t1-s1,t1-s2",
                               },
                               follow=True)
        assert response.redirect_chain[-1][0].endswith("?wybrane=t1-s2&email=b%40example.com#mapa")
        assert "zostało właśnie zajęte" in response.content.decode()

    def test_change_seats_page_and_post(self, client, seated_event):
        order = buy(seated_event, ["t1-s1"])
        assert client.get(f"/zamowienie/{order.id}/zmien-miejsca/").status_code == 200
        response = client.post(f"/zamowienie/{order.id}/zmien-miejsca/", {"seats": "t3-s2"})
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.seat_keys == ["t3-s2"]
        states = client.get(
            f"/wydarzenia/{seated_event.slug}/miejsca.json?zamowienie={order.id}"
        ).json()["states"]
        assert states["t3-s2"] == "mine"

    def test_order_page_lists_seats(self, client, seated_event):
        order = buy(seated_event, ["t1-s1"])
        html = client.get(f"/zamowienie/{order.id}/").content.decode()
        assert "Stolik 1 · miejsce 1" in html and "Zmień miejsca" in html

    def test_general_event_untouched(self, client):
        event = make_event()
        make_pool(event)
        html = client.get(f"/wydarzenia/{event.slug}/").content.decode()
        assert 'name="quantity"' in html and "sm-buy-config" not in html


class TestStaffViews:
    @pytest.fixture
    def manager_client(self, client, django_user_model):
        setup_roles()
        user = django_user_model.objects.create_user("mgr", password="x", is_staff=True)
        user.groups.add(Group.objects.get(name="MANAGER"))
        client.login(username="mgr", password="x")
        return client

    @pytest.fixture
    def door_client(self, client, django_user_model):
        setup_roles()
        user = django_user_model.objects.create_user("door", password="x")
        user.groups.add(Group.objects.get(name="DOOR_STAFF"))
        client.login(username="door", password="x")
        return client

    def test_library_editor_and_event_map_render(self, manager_client, layout, seated_event):
        for url in ["/admin/seating/seatinglayout/",
                    f"/admin/seating/seatinglayout/{layout.pk}/edytor/",
                    f"/admin/seating/seatinglayout/wydarzenie/{seated_event.pk}/"]:
            html = manager_client.get(url).content.decode()
            assert "data-nav-back" in html, url  # Wstecz + Start + path
        assert "Plany sali" in manager_client.get("/admin/").content.decode()

    def test_save_layout_json(self, manager_client, layout):
        data = small_layout()
        data["objects"][0]["label"] = "10"
        response = manager_client.post(
            f"/admin/seating/seatinglayout/{layout.pk}/dane/",
            json.dumps({"name": "Nowa nazwa", "data": data}), content_type="application/json")
        assert response.json()["ok"]
        layout.refresh_from_db()
        assert layout.name == "Nowa nazwa" and layout.data["objects"][0]["label"] == "10"

    def test_save_rejects_invalid_layout(self, manager_client, layout):
        data = small_layout()
        data["objects"][1]["label"] = "1"
        response = manager_client.post(
            f"/admin/seating/seatinglayout/{layout.pk}/dane/",
            json.dumps({"data": data}), content_type="application/json")
        assert response.status_code == 400 and not response.json()["ok"]

    def test_save_as_new(self, manager_client, layout):
        response = manager_client.post(
            f"/admin/seating/seatinglayout/{layout.pk}/zapisz-jako/",
            json.dumps({"name": "Wersja 2", "data": small_layout()}),
            content_type="application/json")
        assert response.json()["ok"]
        assert SeatingLayout.objects.get(name="Wersja 2").based_on == layout

    def test_attach_via_event_map(self, manager_client, layout):
        event = make_event(status="DRAFT")
        make_pool(event, capacity=10)
        response = manager_client.post(f"/admin/seating/seatinglayout/wydarzenie/{event.pk}/",
                                       {"action": "attach", "layout": layout.pk}, follow=True)
        event.refresh_from_db()
        assert event.is_seated and event.capacity_total == 18
        assert "mniejsza niż liczba miejsc" in response.content.decode()

    def test_state_and_actions_json(self, manager_client, seated_event):
        order = buy(seated_event, ["t1-s1"])
        mark_order_paid(order)
        base = f"/admin/seating/seatinglayout/wydarzenie/{seated_event.pk}/"
        state = manager_client.get(base + "stan.json").json()["states"]
        assert state["t1-s1"]["email"] == "a@example.com" and state["t1-s1"]["ticket"]
        post = lambda body: manager_client.post(  # noqa: E731
            base + "akcja/", json.dumps(body), content_type="application/json").json()
        assert post({"action": "block", "seats": ["t1-s3"], "note": "VIP"})["ok"]
        assert post({"action": "move", "from": "t1-s1", "to": "t1-s2"})["ok"]
        assert not post({"action": "move", "from": "t1-s2", "to": "t1-s3"})["ok"]  # blocked

    def test_door_staff_has_no_access(self, door_client, layout, seated_event):
        assert door_client.get("/admin/seating/seatinglayout/").status_code in (302, 403)
        assert door_client.get(
            f"/admin/seating/seatinglayout/wydarzenie/{seated_event.pk}/stan.json"
        ).status_code in (302, 403)

    def test_seed_command(self):
        from django.core.management import call_command

        call_command("seed_seating_demo")
        call_command("seed_seating_demo")  # idempotent
        event = Event.objects.get(slug="kabaret-miejsca-numerowane")
        assert event.is_seated and event.capacity_total == 64


class TestDoorAndTickets:
    def test_scanner_manifest_and_lists_show_seat(self, seated_event):
        import io

        from apps.checkin.services import offline_manifest_tickets, write_emergency_list

        order = buy(seated_event, ["t1-s1"])
        mark_order_paid(order)
        assert offline_manifest_tickets(seated_event)[0]["seat"] == "Stolik 1 · miejsce 1"
        buffer = io.StringIO()
        write_emergency_list(seated_event, buffer)
        assert "Stolik 1 · miejsce 1" in buffer.getvalue()

    def test_ticket_email_and_pdf_contain_seat(self, seated_event):
        from apps.tickets.models import EmailOutbox
        from apps.tickets.services import generate_tickets_pdf

        order = buy(seated_event, ["t1-s1"])
        mark_order_paid(order)
        email = EmailOutbox.objects.get(subject__startswith="Twoje bilety")
        assert "Stolik 1 · miejsce 1" in email.body_text and "Stolik 1" in email.body_html
        assert generate_tickets_pdf(order).startswith(b"%PDF")


class TestRoomSettings:
    """'Ustawienia sali': hide elements / draw own ones; ADMIN only (05.10)."""

    @pytest.fixture
    def imported_room(self):
        from django.core.management import call_command

        call_command("import_hall")
        return Room.objects.get()

    def test_overrides_hide_and_add_and_survive_reimport(self, imported_room):
        from django.core.management import call_command

        from apps.seating.room import save_overrides

        save_overrides(imported_room, {
            "hidden": ["VEN_COLUMN_BAR_4", "nie-istnieje"],
            "custom": [{"id": "c1", "name": "Realizator", "kind": "obstacle",
                        "points": [[300, 1700], [500, 1700], [500, 1800], [300, 1800]]}],
        })
        ids = {o["id"] for o in imported_room.geometry["obstacles"]}
        assert "VEN_COLUMN_BAR_4" not in ids and "c1" in ids
        assert imported_room.overrides["hidden"] == ["VEN_COLUMN_BAR_4"]  # unknown dropped
        call_command("import_hall")
        imported_room.refresh_from_db()
        assert "c1" in {o["id"] for o in imported_room.geometry["obstacles"]}
        # a drawn obstacle blocks tables like the built-in ones
        _, warnings = validate_layout(
            {"objects": [table("t1", "1", 400, 1750)]}, imported_room.geometry)
        assert any("Realizator" in w["message"] for w in warnings)

    def test_bad_custom_element_rejected(self, imported_room):
        from apps.seating.room import save_overrides

        with pytest.raises(ValidationError):
            save_overrides(imported_room, {"custom": [{"id": "c1", "name": "", "kind": "area",
                                                       "points": [[0, 0], [1, 1], [2, 0]]}]})

    def test_manager_can_edit_the_hall(self, client, django_user_model, imported_room):
        # 06.10.2026: the manager edits the hall and saves hall versions
        # (only deleting a hall version stays with ADMIN)
        setup_roles()
        url = f"/admin/seating/seatinglayout/sala/{imported_room.pk}/"
        body = json.dumps({"hidden": ["VEN_COLUMN_BAR_4"], "custom": []})
        mgr = django_user_model.objects.create_user("mgr2", password="x", is_staff=True)
        mgr.groups.add(Group.objects.get(name="MANAGER"))
        assert not mgr.has_perm("seating.delete_room")
        client.login(username="mgr2", password="x")
        assert "Odblokuj edycję sali" in client.get(url).content.decode()
        assert client.post(url, body, content_type="application/json").json()["ok"]
        imported_room.refresh_from_db()
        assert imported_room.overrides["hidden"] == ["VEN_COLUMN_BAR_4"]
        reset = client.post(url, json.dumps({"reset": True}), content_type="application/json")
        assert reset.json()["ok"]
        imported_room.refresh_from_db()
        assert imported_room.overrides == {}


# --- price zones A/B/C (05.10) ---------------------------------------------------------


def zoned_layout():
    data = small_layout()
    data["zones"] = [{"code": "A", "name": "przy scenie", "percent": 120},
                     {"code": "B", "name": "", "percent": 100},
                     {"code": "C", "name": "tańsze", "percent": 80}]
    data["objects"][0]["category"] = "A"  # t1
    data["objects"][2]["category"] = "C"  # t3 (BOTH)
    data["objects"][3]["category"] = "B"  # row A
    return data  # t2 (TABLE) has no zone -> 100 %


@pytest.fixture
def zoned_event(room):
    lay = SeatingLayout(room=room, name="Strefy")
    services.save_layout(lay, zoned_layout())
    event = make_event(status="DRAFT", max_tickets_per_order=6)
    make_pool(event, capacity=100, price_gross=Decimal("89.00"))
    services.attach_layout(event, lay)
    event.status = "PUBLISHED"
    event.save()
    return event


class TestPriceZones:
    def test_zone_price_rounding(self):
        from apps.seating.layout import zone_price

        assert zone_price(Decimal("89.00"), 120) == Decimal("107.00")  # 106.80 -> 107
        assert zone_price(Decimal("89.00"), 80) == Decimal("71.00")  # 71.20 -> 71
        assert zone_price(Decimal("89.50"), 100) == Decimal("89.50")  # base stays exact

    def test_layout_validation(self, room):
        data = zoned_layout()
        data["objects"][1]["category"] = "X"
        errors, _ = validate_layout(data)
        assert any("nieznana strefa" in e["message"] for e in errors)
        data = zoned_layout()
        data["zones"][1]["code"] = "A"
        assert validate_layout(data)[0]
        _, warnings = validate_layout(zoned_layout(), room.geometry)
        assert any("nie ma strefy cenowej" in w["message"] for w in warnings)  # t2

    def test_order_amount_is_sum_of_zone_prices(self, zoned_event):
        order = buy(zoned_event, ["t1-s1", "t3-s1", "r1-s1", "t2-s1", "t2-s2", "t2-s3",
                                  "t2-s4"])
        assert order.seat_prices == ["107.00", "89.00", "89.00", "89.00", "89.00", "71.00",
                                     "89.00"]
        assert order.amount_gross == Decimal("623.00")
        mark_order_paid(order)
        prices = {t.seat.seat_key: t.price_gross for t in order.tickets.all()}
        assert prices["t1-s1"] == Decimal("107.00") and prices["t3-s1"] == Decimal("71.00")

    def test_partial_refund_returns_the_ticket_price(self, zoned_event):
        from apps.orders import refunds

        order = buy(zoned_event, ["t1-s1", "t3-s1", "r1-s1"])
        mark_order_paid(order)
        ticket = order.tickets.get(seat__seat_key="t1-s1")
        refund = refunds.refund_tickets(order, [ticket])
        assert refund.amount == Decimal("107.00")
        order.refresh_from_db()
        assert order.seat_keys == ["t3-s1", "r1-s1"] and order.seat_prices == ["71.00", "89.00"]

    def test_buyer_swap_only_within_same_zones(self, zoned_event):
        order = buy(zoned_event, ["t1-s1"])
        with pytest.raises(order_services.PurchaseError, match="strefach cenowych"):
            order_services.change_pending_order_seats(order, ["t3-s1"])
        order_services.change_pending_order_seats(order, ["t1-s2"])
        order.refresh_from_db()
        assert order.seat_prices == ["107.00"] and order.amount_gross == Decimal("107.00")

    def test_stripe_line_items_per_price(self, zoned_event):
        from apps.orders.providers.stripe_provider import _line_items

        order = buy(zoned_event, ["t1-s1", "t1-s2", "t3-s1"])
        lines = [(li["price_data"]["unit_amount"], li["quantity"]) for li in _line_items(order)]
        assert lines == [(10700, 2), (7100, 1)]
        assert sum(a * q for a, q in lines) == int(order.amount_gross * 100)

    def test_event_page_shows_zone_prices(self, client, zoned_event):
        html = client.get(f"/wydarzenia/{zoned_event.slug}/").content.decode()
        assert "Ceny wg strefy" in html and "107.00" in html and "71.00" in html
        config = html.split('id="sm-buy-config"')[1]
        assert '"zone_prices"' in config


class TestEditorStaleSave:
    def test_save_from_outdated_tab_is_refused(self, admin_client, layout):
        url = f"/admin/seating/seatinglayout/{layout.pk}/dane/"
        old_version = layout.updated_at.isoformat()
        ok = admin_client.post(url, json.dumps({"data": small_layout(), "version": old_version}),
                               content_type="application/json").json()
        assert ok["ok"] and ok["version"] != old_version
        stale = admin_client.post(url, json.dumps({"data": small_layout(), "version": old_version}),
                                  content_type="application/json")
        assert stale.status_code == 409 and stale.json()["stale"]


# --- 06.10: layout changes carried over to upcoming events, hall versions, print -------


@pytest.fixture
def staff_client(client, django_user_model):
    setup_roles()
    user = django_user_model.objects.create_user("mgr6", password="x", is_staff=True)
    user.groups.add(Group.objects.get(name="MANAGER"))
    client.login(username="mgr6", password="x")
    return client


def _attached(room, *, sold_key=None, start_days=5):
    lay = SeatingLayout(room=room, name="Sync")
    services.save_layout(lay, small_layout())
    event = make_event(status="DRAFT", start_at=timezone.now() + timedelta(days=start_days))
    make_pool(event, capacity=100)
    services.attach_layout(event, lay)
    if sold_key:
        order = make_order(event, event.pools.first())
        EventSeat.objects.filter(event=event, seat_key=sold_key).update(
            status=SeatStatus.SOLD, order=order)
    return lay, event


class TestLayoutPropagation:
    def _without_first_table(self, lay):
        data = json.loads(json.dumps(lay.data))
        removed = data["objects"].pop(0)
        return data, [seat["id"] for seat in removed["seats"]]

    def test_save_asks_first_then_updates_upcoming_event(self, staff_client, room):
        lay, event = _attached(room)
        data, removed = self._without_first_table(lay)
        url = f"/admin/seating/seatinglayout/{lay.pk}/dane/"
        first = staff_client.post(url, json.dumps({"data": data}),
                                  content_type="application/json").json()
        assert first["confirm"] and first["events"][0]["id"] == event.pk
        assert first["events"][0]["removed"] == len(removed)
        lay.refresh_from_db()
        assert len(lay.data["objects"]) == len(data["objects"]) + 1  # nothing saved yet
        done = staff_client.post(url, json.dumps({"data": data, "confirm": True}),
                                 content_type="application/json").json()
        assert done["ok"] and done["updated_events"] == 1
        assert not EventSeat.objects.filter(event=event, seat_key__in=removed).exists()
        event.refresh_from_db()
        assert event.capacity_total == EventSeat.objects.filter(event=event).count()
        assert len(event.seat_map.layout_snapshot["objects"]) == len(data["objects"])

    def test_sold_seat_that_would_disappear_keeps_event_untouched(self, staff_client, room):
        lay, _ = _attached(room)
        data, removed = self._without_first_table(lay)
        _, event = _attached(room, sold_key=removed[0])
        event.seat_map.source_layout = lay
        event.seat_map.save()
        url = f"/admin/seating/seatinglayout/{lay.pk}/dane/"
        done = staff_client.post(url, json.dumps({"data": data, "confirm": True}),
                                 content_type="application/json").json()
        assert done["ok"] and done["refused_events"] == 1 and "NIE zmieniono" in done["message"]
        assert EventSeat.objects.filter(event=event, seat_key=removed[0]).exists()

    def test_kept_seats_keep_status_and_past_events_untouched(self, staff_client, room):
        lay, event = _attached(room)
        key = EventSeat.objects.filter(event=event).last().seat_key
        services.block_seats(event, [key], "goście")
        _, past = _attached(room, start_days=-3)
        past.seat_map.source_layout = lay
        past.seat_map.save()
        data, removed = self._without_first_table(lay)
        url = f"/admin/seating/seatinglayout/{lay.pk}/dane/"
        staff_client.post(url, json.dumps({"data": data, "confirm": True}),
                          content_type="application/json")
        seat = EventSeat.objects.get(event=event, seat_key=key)
        assert seat.status == SeatStatus.BLOCKED and seat.block_note == "goście"
        assert EventSeat.objects.filter(event=past, seat_key__in=removed).exists()

    def test_no_upcoming_events_saves_without_question(self, staff_client, room):
        lay = SeatingLayout(room=room, name="Wolny")
        services.save_layout(lay, small_layout())
        url = f"/admin/seating/seatinglayout/{lay.pk}/dane/"
        result = staff_client.post(url, json.dumps({"data": small_layout()}),
                                   content_type="application/json").json()
        assert result["ok"] and result["message"] == "Zapisano."


class TestLibraryAndPrint:
    def test_library_summary_usage_link_and_room_versions(self, staff_client, room):
        lay, event = _attached(room)
        html = staff_client.get("/admin/seating/seatinglayout/").content.decode()
        assert 'id="nav-sidebar"' not in html
        assert "Sala:" not in html
        assert f"/admin/seating/seatinglayout/{lay.pk}/wydarzenia/" in html
        assert "tylko całe stoliki: 2" in html.lower()  # sale rules in plain words
        assert "Wersje sali" in html
        events = staff_client.get(f"/admin/seating/seatinglayout/{lay.pk}/wydarzenia/")
        assert event.title in events.content.decode()

    def test_print_views(self, staff_client, room):
        lay, event = _attached(room)
        page = staff_client.get(f"/admin/seating/seatinglayout/{lay.pk}/druk/").content.decode()
        assert "Drukuj / zapisz PDF" in page and lay.name in page
        page = staff_client.get(
            f"/admin/seating/seatinglayout/wydarzenie/{event.pk}/druk/").content.decode()
        assert event.title in page and "sprzedane:" in page

    def test_room_version_and_switching_layout_room(self, staff_client, room):
        lay, event = _attached(room)
        response = staff_client.post(f"/admin/seating/seatinglayout/sala/{room.pk}/nowa-wersja/",
                                     {"name": "Sala z realizatorem na parterze"})
        assert response.status_code == 302
        version = Room.objects.get(name="Sala z realizatorem na parterze")
        assert version.geometry == room.geometry and version.slug != room.slug
        url = f"/admin/seating/seatinglayout/{lay.pk}/sala/"
        first = staff_client.post(url, json.dumps({"room": version.pk}),
                                  content_type="application/json").json()
        assert first["confirm"]
        staff_client.post(url, json.dumps({"room": version.pk, "confirm": True}),
                          content_type="application/json")
        lay.refresh_from_db()
        event.seat_map.refresh_from_db()
        assert lay.room == version
        assert event.seat_map.room_snapshot["name"] == version.name


class TestCancellationMove:
    def test_moved_order_gets_the_same_seats_on_the_new_date(self, layout):
        from apps.orders import cancellation
        from apps.orders.models import CancellationStatus

        def make_seated(days):
            event = make_event(status="DRAFT", max_tickets_per_order=4,
                               start_at=timezone.now() + timedelta(days=days))
            make_pool(event, capacity=100, price_gross=Decimal("50.00"))
            services.attach_layout(event, layout)
            event.status = "PUBLISHED"
            event.save()
            return event

        old, new = make_seated(7), make_seated(30)
        keys = [s.seat_key for s in EventSeat.objects.filter(event=old).order_by("sort_index")[:2]]
        order = buy(old, keys)
        assert mark_order_paid(order)
        cancellation.cancel_event_for_buyers(old, replacement=new)
        # someone bought the first of those seats on the new date meanwhile
        other = buy(new, [keys[0]], email="ktos@example.com")
        assert mark_order_paid(other)
        cancellation.move_to_replacement(order)
        order.refresh_from_db()
        assert order.cancellation_status == CancellationStatus.MOVED
        assert keys[1] in order.seat_keys and keys[0] not in order.seat_keys
        assert len(order.seat_keys) == 2
        assert EventSeat.objects.filter(event=new, order=order, status=SeatStatus.SOLD).count() == 2
        assert not EventSeat.objects.filter(event=old, order=order).exists()
        assert sorted(t.seat.seat_key for t in order.tickets.all()) == sorted(order.seat_keys)
