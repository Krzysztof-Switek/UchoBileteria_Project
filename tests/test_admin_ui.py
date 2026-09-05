from decimal import Decimal

import pytest

from apps.auditlog.models import AuditLog
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
    def test_add_form_hides_pools_and_live_stats(self, admin_client_logged_in):
        body = admin_client_logged_in.get("/admin/events/event/add/").content.decode()
        assert "pools-TOTAL_FORMS" not in body  # no ticket-pool inline yet
        assert "Aktualizowane automatycznie na podstawie zamówień" not in body
        assert "Okno sprzedaży i limity" in body  # base fieldsets still present

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
        assert "2. Opis i miejsce" in body
        assert "3. Termin wydarzenia" in body
        assert "4. Sprzedaż" in body
        assert "Reguła aktywacji pul" in body  # step-4 hint text present in the JS payload

    def test_change_form_has_no_wizard(self, admin_client_logged_in):
        event = make_event()
        body = admin_client_logged_in.get(
            f"/admin/events/event/{event.pk}/change/"
        ).content.decode()
        assert 'id="ucho-wizard"' not in body

    def test_pool_fieldset_explains_activation_rule(self, admin_client_logged_in):
        event = make_event()
        body = admin_client_logged_in.get(
            f"/admin/events/event/{event.pk}/change/"
        ).content.decode()
        assert "Reguła aktywacji" in body
        assert "się wyprzeda" in body


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
        labels = ["Pulpit", "Wydarzenia", "Zamówienia", "Bilety", "Odprawa", "Raporty", "Dziennik"]
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

    def test_shows_nearest_event_with_fill_bar(self, admin_client_logged_in):
        event = make_event(title="Nadchodzący koncert", capacity_total=100, sold_total=25)
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert "Najbliższe wydarzenie" in body
        assert event.title in body
        assert "25 / 100 miejsc" in body

    def test_fill_bar_width_uses_dot_decimals_not_locale_commas(self, admin_client_logged_in):
        # A bug: Polish locale renders {{ fill_pct }} with a comma ("0,2%"),
        # which is invalid CSS — the browser drops the whole width
        # declaration and the bar silently renders at 100% instead.
        make_event(capacity_total=500, sold_total=1)
        body = admin_client_logged_in.get("/admin/").content.decode()
        assert 'style="width:0.2%"' in body
        assert 'style="width:0,2%"' not in body

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
