from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.checkin.services import ScanResult, check_in_by_short_code
from apps.events.models import EventStatus
from apps.orders import refunds
from apps.orders.models import Order, OrderStatus, PaymentEvent
from apps.reports import services as report_services
from apps.tickets.models import EmailOutbox, TicketStatus
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


def paid_demo_order(client, event=None, quantity=2, email="k@example.com"):
    if event is None:
        event = make_event()
        make_pool(event)
    client.post(f"/kup/{event.slug}/", {"buyer_email": email, "quantity": quantity})
    order = Order.objects.latest("created_at")
    client.post(f"/kasa-demo/{order.id}/zaplac/")
    order.refresh_from_db()
    return event, order


class TestRefundFlow:
    def test_refund_marks_everything_and_releases_capacity(self, client):
        event, order = paid_demo_order(client, quantity=2)
        event.refresh_from_db()
        assert event.sold_total == 2

        refunds.refund_order(order)
        order.refresh_from_db()
        event.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED
        assert order.refunded_at is not None
        assert event.sold_total == 0
        for ticket in order.tickets.all():
            assert ticket.status == TicketStatus.REFUNDED

        # refund confirmation e-mail queued (in addition to the ticket e-mail)
        subjects = list(
            EmailOutbox.objects.filter(order=order).values_list("subject", flat=True)
        )
        assert any("Zwrot" in s for s in subjects)

        # refund recorded in the payment event log
        assert PaymentEvent.objects.filter(
            order=order, event_type="refund.succeeded"
        ).exists()

    def test_refunded_ticket_cannot_check_in(self, client):
        event, order = paid_demo_order(client, quantity=1)
        refunds.refund_order(order)
        ticket = order.tickets.get()
        result, _ = check_in_by_short_code(ticket.short_code, event)
        assert result == ScanResult.REFUNDED

    def test_double_refund_rejected(self, client):
        event, order = paid_demo_order(client)
        refunds.refund_order(order)
        order.refresh_from_db()
        with pytest.raises(ValidationError):
            refunds.refund_order(order)

    def test_unpaid_order_cannot_be_refunded(self):
        event = make_event()
        make_pool(event)
        from apps.orders import services as order_services
        order = order_services.create_order(event, "k@example.com", 1)
        with pytest.raises(ValidationError):
            refunds.refund_order(order)

    def test_checked_in_ticket_survives_refund_as_checked_in(self, client):
        """Refund after entry: used tickets stay CHECKED_IN (history intact)."""
        event, order = paid_demo_order(client, quantity=2)
        first, second = order.tickets.all()
        check_in_by_short_code(first.short_code, event)
        refunds.refund_order(order)
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.status == TicketStatus.CHECKED_IN
        assert second.status == TicketStatus.REFUNDED


class TestEventCancellation:
    def test_cancel_event_refunds_all_paid_orders(self, client):
        event = make_event()
        make_pool(event, capacity=50)
        _, order1 = paid_demo_order(client, event=event, email="a@example.com")
        _, order2 = paid_demo_order(client, event=event, email="b@example.com")
        # plus one unpaid order
        from apps.orders import services as order_services
        pending = order_services.create_order(event, "c@example.com", 1)

        summary = refunds.cancel_event_with_refunds(event)
        assert summary == {"refunded": 2, "failed": 0, "cancelled_unpaid": 1}

        event.refresh_from_db()
        assert event.status == EventStatus.CANCELLED
        for order in (order1, order2):
            order.refresh_from_db()
            assert order.status == OrderStatus.REFUNDED
        pending.refresh_from_db()
        assert pending.status == OrderStatus.CANCELLED


class TestReports:
    def test_cash_flow_reconciles_with_payments(self, client):
        event = make_event()
        make_pool(event, capacity=50, price_gross=Decimal("60.00"))
        _, order1 = paid_demo_order(client, event=event, quantity=2)  # +120
        _, order2 = paid_demo_order(client, event=event, quantity=1)  # +60
        refunds.refund_order(order2)  # -60

        rows = report_services.cash_flow_report(is_demo=True)
        totals = report_services.cash_flow_totals(rows)
        assert totals["inflow"] == Decimal("180.00")
        assert totals["outflow"] == Decimal("60.00")
        assert totals["net"] == Decimal("120.00")

    def test_sales_report_counts(self, client):
        event = make_event()
        make_pool(event, capacity=50)
        _, order1 = paid_demo_order(client, event=event, quantity=2)
        _, order2 = paid_demo_order(client, event=event, quantity=1)
        refunds.refund_order(order2)

        [row] = report_services.event_sales_report(is_demo=True)
        assert row["orders_paid"] == 1
        assert row["orders_refunded"] == 1
        assert row["issued"] == 2
        assert row["refunded"] == 1

    def test_demo_and_live_data_never_mix(self, client):
        event, order = paid_demo_order(client)
        live_rows = report_services.cash_flow_report(is_demo=False)
        assert live_rows == []
        live_sales = report_services.event_sales_report(is_demo=False)
        assert live_sales == []

    def test_dashboard_requires_staff(self, client, django_user_model):
        response = client.get("/raporty/")
        assert response.status_code == 302  # redirected to login

        django_user_model.objects.create_user("zwykly", password="x")
        client.login(username="zwykly", password="x")
        assert client.get("/raporty/").status_code == 302  # still no access

    def test_dashboard_and_csv_for_staff(self, client, django_user_model):
        from django.contrib.auth.models import Permission

        event, order = paid_demo_order(client)
        user = django_user_model.objects.create_user("szef", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_order"))
        client.login(username="szef", password="x")

        page = client.get("/raporty/?tryb=demo").content.decode()
        assert "Przepływy pieniężne" in page
        assert event.title in page

        csv_resp = client.get("/raporty/przeplywy.csv?tryb=demo")
        assert csv_resp.status_code == 200
        assert "Wpłaty" in csv_resp.content.decode("utf-8-sig")

        sales_resp = client.get("/raporty/sprzedaz.csv?tryb=demo")
        assert event.title in sales_resp.content.decode("utf-8-sig")
