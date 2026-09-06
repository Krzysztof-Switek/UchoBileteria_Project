from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.events.models import Event, EventStatus
from apps.orders.models import Order, OrderStatus
from tests.factories import make_event, make_order, make_pool, make_ticket

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client_logged_in(client, django_user_model):
    admin = django_user_model.objects.create_superuser(
        "admin_ui", email="admin_ui@klub.pl", password="x"
    )
    client.force_login(admin)
    return client


def paid_order_via_purchase_flow(client, event=None, quantity=1, email="k@example.com"):
    # Refunding decrements pool/event sold counters, so a refund test needs an
    # order that actually went through the purchase pipeline (unlike a bare
    # make_order(status=PAID), which never incremented them in the first place).
    if event is None:
        event = make_event()
        make_pool(event)
    client.post(f"/kup/{event.slug}/", {"buyer_email": email, "quantity": quantity})
    order = Order.objects.latest("created_at")
    client.post(f"/kasa-demo/{order.id}/zaplac/")
    order.refresh_from_db()
    return event, order


class TestEventAdminAddVsChange:
    def test_add_form_shows_pools_but_hides_live_stats(self, admin_client_logged_in):
        body = admin_client_logged_in.get("/admin/events/event/add/").content.decode()
        assert "pools-TOTAL_FORMS" in body  # first pool created together with the event
        assert "Aktualizowane automatycznie na podstawie zamówień" not in body
        assert 'id="id_max_tickets_per_order"' in body  # moved into the top fieldset

    def test_change_form_shows_pools_and_live_stats(self, admin_client_logged_in):
        event = make_event()
        body = admin_client_logged_in.get(
            f"/admin/events/event/{event.pk}/change/"
        ).content.decode()
        assert "pools-TOTAL_FORMS" in body
        assert "Aktualizowane automatycznie na podstawie zamówień" in body

    def test_add_form_has_wizard_steps(self, admin_client_logged_in):
        # OBS-08: multi-step wizard only makes sense while creating an event.
        body = admin_client_logged_in.get("/admin/events/event/add/").content.decode()
        assert 'id="ucho-wizard"' in body
        assert "1. Podstawy" in body
        assert "2. Termin wydarzenia" in body
        assert "3. Sprzedaż" in body
        assert "Pule biletowe" in body  # pools fieldset now renders on add too

    def test_change_form_has_no_wizard(self, admin_client_logged_in):
        event = make_event()
        body = admin_client_logged_in.get(
            f"/admin/events/event/{event.pk}/change/"
        ).content.decode()
        assert 'id="ucho-wizard"' not in body


def _event_add_payload(**overrides):
    now = timezone.now()

    def dt(offset_days, hour=20):
        d = now + timedelta(days=offset_days)
        return (d.strftime("%d.%m.%Y"), f"{hour}:00")

    gates_date, gates_time = dt(7, hour=19)
    start_date, start_time = dt(7, hour=20)
    data = {
        "title": "Nowy koncert",
        "description": "",
        "gates_open_at_0": gates_date,
        "gates_open_at_1": gates_time,
        "start_at_0": start_date,
        "start_at_1": start_time,
        "end_at_0": "",
        "end_at_1": "",
        "max_tickets_per_order": "10",
        "pools-TOTAL_FORMS": "1",
        "pools-INITIAL_FORMS": "0",
        "pools-MIN_NUM_FORMS": "0",
        "pools-MAX_NUM_FORMS": "1000",
        "pools-0-id": "",
        "pools-0-event": "",
        "pools-0-name": "Pula 1",
        "pools-0-price_gross": "50.00",
        "pools-0-capacity": "100",
        "pools-0-sales_start_at_0": "",
        "pools-0-sales_start_at_1": "",
        "pools-0-sales_end_at_0": "",
        "pools-0-sales_end_at_1": "",
        "pools-0-on_sellout": "ACTIVATE_NEXT",
    }
    data.update(overrides)
    return data


class TestEventAdminCreateWithPool:
    """OBS-08 follow-up: the first pool is created together with the event."""

    def test_create_event_with_pool_success(self, admin_client_logged_in):
        resp = admin_client_logged_in.post(
            "/admin/events/event/add/", _event_add_payload(), follow=True
        )
        assert resp.status_code == 200
        event = Event.objects.get(title="Nowy koncert")
        assert event.pools.count() == 1
        pool = event.pools.get()
        assert pool.name == "Pula 1"
        assert pool.capacity == 100
        assert pool.price_gross == Decimal("50.00")

    def test_publish_button_publishes_when_valid(self, admin_client_logged_in):
        now = timezone.now()
        resp = admin_client_logged_in.post(
            "/admin/events/event/add/",
            _event_add_payload(
                **{
                    "pools-0-sales_start_at_0": (now - timedelta(days=1)).strftime("%d.%m.%Y"),
                    "pools-0-sales_start_at_1": "00:00",
                    "pools-0-sales_end_at_0": now.strftime("%d.%m.%Y"),
                    "pools-0-sales_end_at_1": "00:00",
                    "_publish": "1",
                }
            ),
            follow=True,
        )
        assert resp.status_code == 200
        event = Event.objects.get(title="Nowy koncert")
        assert event.status == EventStatus.PUBLISHED

    def test_publish_button_falls_back_to_draft_when_invalid(self, admin_client_logged_in):
        # Pool has no sales window of its own, so the derived event-level
        # sales_start_at == sales_end_at (both default to start_at) — invalid.
        resp = admin_client_logged_in.post(
            "/admin/events/event/add/",
            _event_add_payload(**{"_publish": "1"}),
            follow=True,
        )
        assert resp.status_code == 200
        event = Event.objects.get(title="Nowy koncert")
        assert event.status == EventStatus.DRAFT
        assert "nie udało się opublikować" in resp.content.decode()

    def test_pool_capacity_sum_over_club_max_rejected(self, admin_client_logged_in):
        # There's no per-event capacity field any more (OBS-08 follow-up) —
        # the only ceiling is the club's fixed capacity.
        resp = admin_client_logged_in.post(
            "/admin/events/event/add/",
            _event_add_payload(**{"pools-0-capacity": "600"}),
        )
        assert resp.status_code == 200  # re-renders the form with an error
        assert "Suma biletów w pulach" in resp.content.decode()
        assert not Event.objects.filter(title="Nowy koncert").exists()

    def test_pool_ending_on_or_after_concert_rejected(self, admin_client_logged_in):
        now = timezone.now()
        resp = admin_client_logged_in.post(
            "/admin/events/event/add/",
            _event_add_payload(
                **{
                    "pools-0-sales_start_at_0": (now + timedelta(days=5)).strftime("%d.%m.%Y"),
                    "pools-0-sales_start_at_1": "00:00",
                    # start_at is now+7 days — an end past that is the concert day itself.
                    "pools-0-sales_end_at_0": (now + timedelta(days=8)).strftime("%d.%m.%Y"),
                    "pools-0-sales_end_at_1": "00:00",
                }
            ),
        )
        assert resp.status_code == 200
        assert "musi kończyć się przed dniem koncertu" in resp.content.decode()
        assert not Event.objects.filter(title="Nowy koncert").exists()

    def test_overlapping_pools_rejected(self, admin_client_logged_in):
        now = timezone.now()

        def d(offset):
            return (now + timedelta(days=offset)).strftime("%d.%m.%Y")

        resp = admin_client_logged_in.post(
            "/admin/events/event/add/",
            _event_add_payload(
                **{
                    "pools-TOTAL_FORMS": "2",
                    "pools-0-capacity": "50",
                    "pools-0-sales_start_at_0": d(1),
                    "pools-0-sales_start_at_1": "00:00",
                    "pools-0-sales_end_at_0": d(3),
                    "pools-0-sales_end_at_1": "00:00",
                    "pools-1-id": "",
                    "pools-1-event": "",
                    "pools-1-name": "Pula 2",
                    "pools-1-price_gross": "80.00",
                    "pools-1-capacity": "10",
                    "pools-1-sales_start_at_0": d(2),
                    "pools-1-sales_start_at_1": "00:00",
                    "pools-1-sales_end_at_0": d(4),
                    "pools-1-sales_end_at_1": "00:00",
                    "pools-1-on_sellout": "ACTIVATE_NEXT",
                }
            ),
        )
        assert resp.status_code == 200
        assert "nachodzą na siebie" in resp.content.decode()
        assert not Event.objects.filter(title="Nowy koncert").exists()


class TestTicketPoolAdminHiddenFromAppList:
    """Pools only ever make sense attached to an event — added/edited via the
    inline on the event's own page, never as a standalone top-level item."""

    def test_not_listed_on_dashboard_app_list(self, admin_client_logged_in):
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert "Pule biletów" not in body

    def test_standalone_add_is_blocked(self, admin_client_logged_in):
        response = admin_client_logged_in.get("/admin/events/ticketpool/add/")
        assert response.status_code == 403

    def test_changelist_still_reachable_directly(self, admin_client_logged_in):
        # Not unregistered — the cross-event list + force-open/close bulk
        # actions still work for whoever navigates here directly.
        event = make_event()
        pool = make_pool(event)
        response = admin_client_logged_in.get("/admin/events/ticketpool/")
        assert response.status_code == 200
        assert pool.name in response.content.decode()


class TestAdminNavigation:
    def test_staff_nav_present_on_index(self, admin_client_logged_in):
        body = admin_client_logged_in.get("/admin/").content.decode()
        labels = ["Start", "Wydarzenia", "Zamówienia", "Bilety", "Odprawa", "Raporty", "Dziennik"]
        for label in labels:
            assert label in body

    def test_global_search_box_present_with_keyboard_shortcut_hint(self, admin_client_logged_in):
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert 'id="global-search-input"' in body
        assert "(/)" in body

    def test_app_sections_ordered_by_workflow_not_alphabet(self, admin_client_logged_in):
        body = admin_client_logged_in.get("/admin/").content.decode()
        # Skip the staff nav bar first — it also links to "Wydarzenia"/"Bilety",
        # which would otherwise match before the actual app-list section headers.
        content = body.split("</nav>", 1)[1]
        # UX-05: events -> orders -> tickets -> auditlog, not alphabetical
        # (which would put Bilety before Wydarzenia and Dziennik before Wydarzenia).
        markers = ["Wydarzenia</a>", "Zamówienia i płatności", "Bilety</a>", "Dziennik zdarzeń"]
        positions = [content.index(marker) for marker in markers]
        assert positions == sorted(positions)


class TestStatusBadges:
    """OBS-02: colour is always paired with the label text, never colour alone."""

    def test_order_status_is_colored_badge(self, admin_client_logged_in):
        event = make_event()
        pool = make_pool(event)
        order = make_order(
            event, pool, status=OrderStatus.REFUNDED, amount_gross=Decimal("60.00")
        )
        body = admin_client_logged_in.get("/admin/orders/order/").content.decode()
        assert str(order.short_id) in body
        assert "Zwrócone</span>" in body
        assert "background:#dc2626" in body  # danger tone for a refund

    def test_ticket_status_is_colored_badge(self, admin_client_logged_in):
        event = make_event()
        pool = make_pool(event)
        order = make_order(event, pool, status=OrderStatus.PAID)
        ticket = make_ticket(order)
        body = admin_client_logged_in.get("/admin/tickets/ticket/").content.decode()
        assert ticket.short_code in body
        assert "Wydany</span>" in body
        assert "background:#059669" in body  # success tone for an issued ticket

    def test_event_sales_state_has_polish_label_not_raw_enum(self, admin_client_logged_in):
        make_event()  # no pools -> CLOSED per apps.events.services.get_sales_state
        body = admin_client_logged_in.get("/admin/events/event/").content.decode()
        assert "Zakończona</span>" in body


class TestOpsDashboard:
    """OBS-01: admin landing page becomes an operations dashboard."""

    def test_events_overview_shows_published_and_draft_by_default(self, admin_client_logged_in):
        published = make_event(title="Nadchodzący koncert", capacity_total=100, sold_total=25)
        draft = make_event(title="Szkic wydarzenia", status=EventStatus.DRAFT)
        finished = make_event(title="Dawny koncert", status=EventStatus.FINISHED)
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert "Wydarzenia" in body
        assert published.title in body
        assert "25 / 100" in body
        assert draft.title in body
        assert finished.title not in body

    def test_events_overview_finished_filter_shows_only_finished(self, admin_client_logged_in):
        published = make_event(title="Nadchodzący koncert")
        finished = make_event(title="Dawny koncert", status=EventStatus.FINISHED)
        body = admin_client_logged_in.get("/admin/?wydarzenia=zakonczone").content.decode()
        assert finished.title in body
        assert published.title not in body

    def test_fill_bar_width_uses_dot_decimals_not_locale_commas(self, admin_client_logged_in):
        # A bug: Polish locale renders {{ fill_pct }} with a comma ("0,2%"),
        # which is invalid CSS — the browser drops the whole width
        # declaration and the bar silently renders at 100% instead.
        make_event(capacity_total=500, sold_total=1)
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert 'style="width:0.2%"' in body
        assert 'style="width:0,2%"' not in body

    def test_recent_actions_sidebar_removed(self, admin_client_logged_in):
        make_event()
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert "Ostatnie działania" not in body and "Recent actions" not in body

    def test_sales_tiles_removed_from_dashboard(self, admin_client_logged_in):
        make_event()
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert "Sprzedaż dziś" not in body
        assert "ucho-stat-grid" not in body

    def test_shows_attention_queue(self, admin_client_logged_in):
        from apps.orders.models import PaymentEvent, PaymentEventStatus, PaymentProviderKind
        from apps.tickets.models import EmailOutbox, EmailStatus

        event = make_event()
        pool = make_pool(event)
        make_order(event, pool, status=OrderStatus.PAYMENT_PENDING)
        PaymentEvent.objects.create(
            provider=PaymentProviderKind.DEMO,
            provider_event_id="evt_1",
            event_type="payment.succeeded",
            processing_status=PaymentEventStatus.ERROR,
            is_demo=True,
        )
        EmailOutbox.objects.create(
            to_email="test@example.com", subject="Test", status=EmailStatus.FAILED
        )

        body = admin_client_logged_in.get("/admin/").content.decode()
        assert "Wymaga uwagi" in body
        assert "płatności w toku" in body
        assert "nieudanych webhooków" in body
        assert "e-maili z błędem" in body

    def test_attention_card_hidden_when_nothing_needs_attention(self, admin_client_logged_in):
        # No empty-state message any more — the whole card just doesn't render.
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert "Wymaga uwagi" not in body
        assert "Brak spraw wymagających uwagi" not in body

    def test_attention_card_renders_after_reports_banner(self, admin_client_logged_in):
        event = make_event()
        pool = make_pool(event)
        make_order(event, pool, status=OrderStatus.PAYMENT_PENDING)
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert body.index("Raporty sprzedaży") < body.index("Wymaga uwagi")

    def test_checkin_shortcut_is_standalone_not_inside_attention_card(self, admin_client_logged_in):
        # Moved out of "Wymaga uwagi": it's a daily shortcut, not a problem
        # to fix, and must show even when nothing needs attention.
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert "Otwórz odprawę" in body
        assert "Wymaga uwagi" not in body  # sanity: attention card absent here


class TestRefundConfirmation:
    """OBS-03: refund is a deliberate, confirmed action — never a single click."""

    def test_refund_button_shown_only_for_paid_order(self, admin_client_logged_in):
        event = make_event()
        pool = make_pool(event)
        paid = make_order(event, pool, status=OrderStatus.PAID)
        pending = make_order(event, pool, status=OrderStatus.PAYMENT_PENDING)

        body = admin_client_logged_in.get(f"/admin/orders/order/{paid.pk}/change/").content.decode()
        assert "Zwróć zamówienie" in body

        body = admin_client_logged_in.get(
            f"/admin/orders/order/{pending.pk}/change/"
        ).content.decode()
        assert "Zwróć zamówienie" not in body

    def test_get_shows_confirmation_without_refunding(self, admin_client_logged_in):
        event = make_event()
        pool = make_pool(event)
        order = make_order(event, pool, status=OrderStatus.PAID, quantity=2)

        body = admin_client_logged_in.get(
            f"/admin/orders/order/{order.pk}/zwrot/"
        ).content.decode()
        assert order.short_id in body
        assert order.buyer_email in body
        assert "unieważni bilety" in body
        assert "Powód zwrotu" in body

        order.refresh_from_db()
        assert order.status == OrderStatus.PAID  # nothing happened yet

    def test_post_confirms_refund_and_logs_reason(self, admin_client_logged_in):
        _, order = paid_order_via_purchase_flow(admin_client_logged_in)

        response = admin_client_logged_in.post(
            f"/admin/orders/order/{order.pk}/zwrot/",
            {"reason": "Klient nie może przyjść"},
        )
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED

        log = AuditLog.objects.get(action="refund.requested")
        assert log.metadata["reason"] == "Klient nie może przyjść"

    def test_cannot_refund_a_non_paid_order(self, admin_client_logged_in):
        event = make_event()
        pool = make_pool(event)
        order = make_order(event, pool, status=OrderStatus.EXPIRED)

        response = admin_client_logged_in.post(f"/admin/orders/order/{order.pk}/zwrot/", {})
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.status == OrderStatus.EXPIRED

    def test_bulk_action_shows_confirmation_before_refunding(self, admin_client_logged_in):
        event = make_event()
        pool = make_pool(event)
        order1 = make_order(event, pool, status=OrderStatus.PAID)
        order2 = make_order(event, pool, status=OrderStatus.PAID)

        response = admin_client_logged_in.post(
            "/admin/orders/order/",
            {
                "action": "refund_selected_orders",
                "_selected_action": [str(order1.pk), str(order2.pk)],
                "index": "0",
            },
        )
        body = response.content.decode()
        assert "Potwierdź zwrot" in body
        assert order1.short_id in body
        assert order2.short_id in body

        order1.refresh_from_db()
        order2.refresh_from_db()
        assert order1.status == OrderStatus.PAID
        assert order2.status == OrderStatus.PAID

    def test_bulk_action_confirmed_refunds_all_selected(self, admin_client_logged_in):
        event, order1 = paid_order_via_purchase_flow(admin_client_logged_in)
        _, order2 = paid_order_via_purchase_flow(admin_client_logged_in, event=event)

        admin_client_logged_in.post(
            "/admin/orders/order/",
            {
                "action": "refund_selected_orders",
                "_selected_action": [str(order1.pk), str(order2.pk)],
                "index": "0",
                "confirm_refund": "yes",
                "reason": "Odwołany support",
            },
        )
        order1.refresh_from_db()
        order2.refresh_from_db()
        assert order1.status == OrderStatus.REFUNDED
        assert order2.status == OrderStatus.REFUNDED
        assert AuditLog.objects.filter(
            action="refund.requested", metadata__reason="Odwołany support"
        ).count() == 2

    def test_bulk_action_skips_non_paid_orders(self, admin_client_logged_in):
        event = make_event()
        pool = make_pool(event)
        paid = make_order(event, pool, status=OrderStatus.PAID)
        expired = make_order(event, pool, status=OrderStatus.EXPIRED)

        response = admin_client_logged_in.post(
            "/admin/orders/order/",
            {
                "action": "refund_selected_orders",
                "_selected_action": [str(paid.pk), str(expired.pk)],
                "index": "0",
            },
        )
        body = response.content.decode()
        assert f"Pominięto (nieopłacone, nic do zwrotu): {expired.short_id}" in body
        # Only the payable order reaches the confirmation table itself
        # (the sidebar app-list also has a bare <table>, so match ours by class).
        table = body.split('<table class="refund-summary">', 1)[1]
        assert paid.short_id in table
        assert expired.short_id not in table

    def test_confirmation_does_not_refund_orders_added_via_get(self, admin_client_logged_in):
        # Sanity: a plain GET on the changelist never touches order state.
        event = make_event()
        pool = make_pool(event)
        order = make_order(event, pool, status=OrderStatus.PAID)
        admin_client_logged_in.get("/admin/orders/order/")
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID
