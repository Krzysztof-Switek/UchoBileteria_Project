"""Refunding selected tickets of a paid order (zwrot części biletów, 05.10.2026)."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.orders import refunds
from apps.orders.models import OrderRefund, OrderStatus, PaymentConfig, PaymentEvent
from apps.orders.payments import mark_order_paid
from apps.orders.services import create_order
from apps.reports.services import cash_flow_report, event_revenue_summary
from apps.seating.models import EventSeat, SeatStatus
from apps.tickets.models import EmailOutbox, TicketStatus
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


@pytest.fixture
def paid_order():
    event = make_event()
    pool = make_pool(event, price_gross=Decimal("60.00"))
    order = create_order(event, "a@example.com", quantity=3)
    mark_order_paid(order)
    order.refresh_from_db()
    return event, pool, order


def tickets_of(order):
    return list(order.tickets.order_by("short_code"))


class TestPartialRefund:
    def test_refund_one_of_three(self, paid_order):
        event, pool, order = paid_order
        first = tickets_of(order)[0]
        refund = refunds.refund_tickets(order, [first], reason="nie może przyjść")
        order.refresh_from_db()
        event.refresh_from_db()
        pool.refresh_from_db()
        assert refund.status == "SUCCEEDED" and refund.amount == Decimal("60.00")
        assert order.status == OrderStatus.PAID  # the rest is still valid
        assert (order.refunded_quantity, order.refunded_amount) == (1, Decimal("60.00"))
        assert (event.sold_total, pool.sold_count) == (2, 2)
        statuses = sorted(t.status for t in order.tickets.all())
        assert statuses == [TicketStatus.ISSUED, TicketStatus.ISSUED, TicketStatus.REFUNDED]
        assert EmailOutbox.objects.filter(subject__startswith="Zwrot części biletów").exists()

    def test_reports_and_cash_flow_count_only_the_refunded_part(self, paid_order):
        event, _, order = paid_order
        refunds.refund_tickets(order, tickets_of(order)[:1])
        money = event_revenue_summary(event, is_demo=True)
        assert money == {"paid": Decimal("120.00"), "refunded": Decimal("60.00")}
        flow = cash_flow_report(is_demo=True)
        assert sum(r["outflow"] for r in flow) == Decimal("60.00")
        assert PaymentEvent.objects.filter(event_type="refund.succeeded",
                                           amount=Decimal("60.00")).count() == 1

    def test_refunding_the_rest_closes_the_order(self, paid_order):
        _, _, order = paid_order
        a, b, c = tickets_of(order)
        refunds.refund_tickets(order, [a])
        refunds.refund_tickets(order, [b, c])
        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED
        assert order.refunded_amount == order.amount_gross
        assert not order.tickets.filter(status=TicketStatus.ISSUED).exists()

    def test_full_refund_after_partial_returns_only_the_rest(self, paid_order):
        event, _, order = paid_order
        refunds.refund_tickets(order, tickets_of(order)[:1])
        order.refresh_from_db()
        refunds.refund_order(order)
        order.refresh_from_db()
        event.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED and event.sold_total == 0
        amounts = sorted(PaymentEvent.objects.filter(
            order=order, event_type="refund.succeeded").values_list("amount", flat=True))
        assert amounts == [Decimal("60.00"), Decimal("120.00")]
        assert event_revenue_summary(event, is_demo=True)["refunded"] == Decimal("180.00")

    def test_all_tickets_of_untouched_order_is_a_full_refund(self, paid_order):
        _, _, order = paid_order
        result = refunds.refund_tickets(order, tickets_of(order))
        order.refresh_from_db()
        assert not isinstance(result, OrderRefund)
        assert order.status == OrderStatus.REFUNDED and not order.refunds.exists()

    def test_checked_in_or_foreign_ticket_rejected(self, paid_order):
        _, _, order = paid_order
        used = tickets_of(order)[0]
        used.status = TicketStatus.CHECKED_IN
        used.save()
        with pytest.raises(ValidationError):
            refunds.refund_tickets(order, [used])
        with pytest.raises(ValidationError):
            refunds.refund_tickets(order, [])

    def test_rounding_last_ticket_takes_the_rest(self):
        event = make_event()
        make_pool(event, price_gross=Decimal("33.33"))
        order = create_order(event, "a@example.com", quantity=3)
        order.amount_gross = Decimal("100.00")  # e.g. a rounded promo total
        order.save()
        mark_order_paid(order)
        a, b, c = tickets_of(order)
        assert refunds.refund_tickets(order, [a]).amount == Decimal("33.33")
        assert refunds.refund_tickets(order, [b]).amount == Decimal("33.33")
        assert refunds.refund_tickets(order, [c]).amount == Decimal("33.34")

    def test_duplicate_webhook_applies_once(self, paid_order):
        from apps.orders.payments import apply_partial_refund

        event, _, order = paid_order
        refund = refunds.refund_tickets(order, tickets_of(order)[:1])
        assert not apply_partial_refund(order, refund.pk)
        event.refresh_from_db()
        assert event.sold_total == 2


class TestSeatedPartialRefund:
    def test_refunded_seat_goes_back_on_sale(self):
        from apps.seating import services as seating
        from apps.seating.models import Room, SeatingLayout

        room = Room.objects.create(slug="s", name="S", width_cm=1130, depth_cm=2230,
                                   geometry={})
        layout = SeatingLayout(room=room, name="L")
        from tests.test_seating import small_layout as build

        seating.save_layout(layout, build())
        event = make_event(status="DRAFT")
        make_pool(event, capacity=50)
        seating.attach_layout(event, layout)
        event.status = "PUBLISHED"
        event.save()
        order = create_order(event, "a@example.com", seat_keys=["t1-s1", "t1-s2"])
        mark_order_paid(order)
        ticket = order.tickets.get(seat__seat_key="t1-s2")
        refunds.refund_tickets(order, [ticket])
        order.refresh_from_db()
        assert order.seat_labels == ["Stolik 1 · miejsce 1"]
        seat = EventSeat.objects.get(event=event, seat_key="t1-s2")
        assert seat.status == SeatStatus.AVAILABLE and seat.order is None
        ticket.refresh_from_db()
        assert ticket.status == TicketStatus.REFUNDED and ticket.seat == seat  # history
        create_order(event, "b@example.com", seat_keys=["t1-s2"])  # sellable again


class TestStripePartial:
    def test_partial_refund_amount_and_webhook_matching(self, monkeypatch):
        import stripe

        from apps.orders.models import PaymentMode
        from apps.orders.providers.stripe_provider import handle_stripe_event

        calls = []
        monkeypatch.setattr(stripe.Refund, "create",
                            lambda **kw: calls.append(kw) or {"id": "re_1"})
        config = PaymentConfig.load()
        config.mode = PaymentMode.LIVE
        config.save()
        event = make_event()
        make_pool(event, price_gross=Decimal("60.00"))
        order = create_order(event, "a@example.com", quantity=3)
        order.provider_order_id = "pi_1"
        order.save()
        mark_order_paid(order)
        refund = refunds.refund_tickets(order, tickets_of(order)[:1])
        assert calls[0]["amount"] == 6000 and calls[0]["payment_intent"] == "pi_1"
        assert refund.status == "REQUESTED"  # Stripe confirms asynchronously
        handle_stripe_event({"id": "evt_1", "type": "charge.refunded",
                             "data": {"object": {"payment_intent": "pi_1",
                                                 "amount_refunded": 6000}}})
        refund.refresh_from_db()
        order.refresh_from_db()
        assert refund.status == "SUCCEEDED" and order.status == OrderStatus.PAID
        # later full refund of the rest: cumulative 18000 -> booked delta 120
        refunds.refund_order(order)
        handle_stripe_event({"id": "evt_2", "type": "charge.refunded",
                             "data": {"object": {"payment_intent": "pi_1",
                                                 "amount_refunded": 18000}}})
        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED
        assert PaymentEvent.objects.get(provider_event_id="evt_2").amount == Decimal("120.00")


class TestAdminScreen:
    def test_partial_refund_page_and_post(self, admin_client, paid_order):
        _, _, order = paid_order
        url = f"/admin/orders/order/{order.pk}/zwrot-biletow/"
        ticket = tickets_of(order)[0]
        html = admin_client.get(url + f"?bilet={ticket.id}").content.decode()
        assert ticket.short_code in html and "checked" in html and "data-nav-back" in html
        response = admin_client.post(url, {"tickets": [str(ticket.id)], "reason": "x"})
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.refunded_quantity == 1
        page = admin_client.get(f"/admin/orders/order/{order.pk}/change/").content.decode()
        assert "Zwróć resztę zamówienia" in page and ticket.short_code in page
