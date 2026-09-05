import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.checkin.services import ScanResult, check_in_by_short_code
from apps.events.models import EventStatus
from apps.orders import refunds
from apps.orders.models import Order, OrderStatus, PaymentEvent
from apps.reports import services as report_services
from apps.tickets.models import EmailOutbox, TicketStatus
from tests.factories import make_event, make_order, make_pool, make_ticket

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

    def test_pool_sales_report_groups_by_pool(self):
        event = make_event()
        pool_a = make_pool(event, name="Early bird", priority=1, price_gross=Decimal("50.00"))
        pool_b = make_pool(event, name="Regular", priority=2, price_gross=Decimal("80.00"))
        make_ticket(
            make_order(event, pool_a, status=OrderStatus.PAID, amount_gross=Decimal("50.00"))
        )
        make_ticket(
            make_order(event, pool_b, status=OrderStatus.PAID, amount_gross=Decimal("80.00"))
        )

        rows = {row["pool"].name: row for row in report_services.pool_sales_report(is_demo=True)}
        assert rows["Early bird"]["revenue_paid"] == Decimal("50.00")
        assert rows["Regular"]["revenue_paid"] == Decimal("80.00")
        assert rows["Early bird"]["orders_paid"] == 1
        assert rows["Regular"]["orders_paid"] == 1

    def test_pool_sales_report_filters_by_event(self):
        event1 = make_event()
        pool1 = make_pool(event1)
        make_ticket(make_order(event1, pool1, status=OrderStatus.PAID))
        event2 = make_event()
        pool2 = make_pool(event2)
        make_ticket(make_order(event2, pool2, status=OrderStatus.PAID))

        rows = report_services.pool_sales_report(is_demo=True, event=event1)
        assert len(rows) == 1
        assert rows[0]["event"] == event1

    def test_event_revenue_summary_matches_sales_report(self, client):
        event, order1 = paid_demo_order(client, quantity=2)
        _, order2 = paid_demo_order(client, event=event, quantity=1)
        refunds.refund_order(order2)

        summary = report_services.event_revenue_summary(event, is_demo=True)
        [row] = report_services.event_sales_report(is_demo=True)
        assert summary["paid"] == row["revenue_paid"]
        assert summary["refunded"] == row["revenue_refunded"]

    def test_event_sales_report_date_range_filter(self):
        now = timezone.now()
        past_event = make_event(
            title="Stare wydarzenie",
            start_at=now - timedelta(days=30),
            sales_start_at=now - timedelta(days=31),
            sales_end_at=now - timedelta(days=29),
        )
        make_ticket(make_order(past_event, make_pool(past_event), status=OrderStatus.PAID))

        future_event = make_event(title="Nowe wydarzenie")
        make_ticket(make_order(future_event, make_pool(future_event), status=OrderStatus.PAID))

        rows = report_services.event_sales_report(is_demo=True, date_from=now.date())
        event_ids = {row["event"].pk for row in rows}
        assert future_event.pk in event_ids
        assert past_event.pk not in event_ids

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
        assert "Kluczowe liczby" in page
        assert event.title in page

        csv_resp = client.get("/raporty/przeplywy.csv?tryb=demo")
        assert csv_resp.status_code == 200
        assert "Wpłaty" in csv_resp.content.decode("utf-8-sig")

        sales_resp = client.get("/raporty/sprzedaz.csv?tryb=demo")
        assert event.title in sales_resp.content.decode("utf-8-sig")

        pool_resp = client.get("/raporty/pule.csv?tryb=demo")
        assert pool_resp.status_code == 200
        assert event.title in pool_resp.content.decode("utf-8-sig")

        future = (timezone.now() + timedelta(days=365)).date().isoformat()
        filtered = client.get(f"/raporty/?tryb=demo&od={future}").content.decode()
        assert event.title not in filtered  # date filter excludes the event


class TestResolvePeriod:
    """RAP-01: one-click report periods. Calendar year/quarters (open decision
    #2 in the UX plan — revisit if the club adopts a different fiscal year)."""

    def test_dzis(self):
        today = date(2026, 5, 15)
        assert report_services.resolve_period("dzis", today) == (today, today)

    def test_7dni(self):
        today = date(2026, 5, 15)
        assert report_services.resolve_period("7dni", today) == (date(2026, 5, 9), today)

    def test_ten_miesiac(self):
        today = date(2026, 5, 15)
        assert report_services.resolve_period("ten_miesiac", today) == (date(2026, 5, 1), today)

    def test_poprzedni_miesiac(self):
        today = date(2026, 5, 15)
        assert report_services.resolve_period("poprzedni_miesiac", today) == (
            date(2026, 4, 1),
            date(2026, 4, 30),
        )

    def test_poprzedni_miesiac_across_year_boundary(self):
        today = date(2026, 1, 15)
        assert report_services.resolve_period("poprzedni_miesiac", today) == (
            date(2025, 12, 1),
            date(2025, 12, 31),
        )

    def test_ten_kwartal(self):
        today = date(2026, 5, 15)  # Q2
        assert report_services.resolve_period("ten_kwartal", today) == (date(2026, 4, 1), today)

    def test_poprzedni_kwartal(self):
        today = date(2026, 5, 15)  # Q2 -> previous is Q1
        assert report_services.resolve_period("poprzedni_kwartal", today) == (
            date(2026, 1, 1),
            date(2026, 3, 31),
        )

    def test_poprzedni_kwartal_across_year_boundary(self):
        today = date(2026, 1, 15)  # Q1 -> previous is Q4 of the prior year
        assert report_services.resolve_period("poprzedni_kwartal", today) == (
            date(2025, 10, 1),
            date(2025, 12, 31),
        )

    def test_ten_rok(self):
        today = date(2026, 5, 15)
        assert report_services.resolve_period("ten_rok", today) == (date(2026, 1, 1), today)

    def test_unknown_period_returns_none(self):
        assert report_services.resolve_period("nieznany", date(2026, 5, 15)) is None

    def test_dashboard_period_shortcut_selects_matching_events(self, client, django_user_model):
        from django.contrib.auth.models import Permission

        event = make_event(title="Impreza w tym roku", start_at=timezone.now())
        make_ticket(make_order(event, make_pool(event), status=OrderStatus.PAID))
        user = django_user_model.objects.create_user("szef_okresy", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_order"))
        client.login(username="szef_okresy", password="x")

        page = client.get("/raporty/?tryb=demo&okres=ten_rok").content.decode()
        assert event.title in page
        assert 'btn-primary font-bold">Ten rok</a>' in page  # active state highlighted


class TestPeriodKPIs:
    """RAP-02: KPIs anchored on payment date, matching cash_flow_report."""

    def test_period_kpis_matches_cash_flow_and_orders(self, client):
        event, order1 = paid_demo_order(client, quantity=2)  # +120
        _, order2 = paid_demo_order(client, event=event, quantity=1)  # +60, then refunded
        refunds.refund_order(order2)

        today = timezone.now().date()
        kpis = report_services.period_kpis(is_demo=True, date_from=today, date_to=today)
        assert kpis["revenue_net"] == Decimal("120.00")  # 180 in, 60 refunded
        assert kpis["refunds"] == Decimal("60.00")
        # order2 is REFUNDED (no longer PAID), so it drops out of "tickets sold"/avg —
        # both stay net-of-refunds, consistent with revenue_net.
        assert kpis["tickets_sold"] == 2
        assert kpis["avg_order_value"] == Decimal("120.00")


class TestPercentChange:
    def test_increase(self):
        assert report_services.percent_change(Decimal("150"), Decimal("100")) == 50.0

    def test_decrease(self):
        assert report_services.percent_change(Decimal("50"), Decimal("100")) == -50.0

    def test_previous_zero_returns_none(self):
        assert report_services.percent_change(Decimal("10"), Decimal("0")) is None
        assert report_services.percent_change(Decimal("0"), Decimal("0")) is None


class TestPreviousPeriod:
    def test_same_length_immediately_before(self):
        assert report_services.previous_period(date(2026, 8, 1), date(2026, 8, 31)) == (
            date(2026, 7, 1),
            date(2026, 7, 31),
        )

    def test_single_day_period(self):
        assert report_services.previous_period(date(2026, 8, 21), date(2026, 8, 21)) == (
            date(2026, 8, 20),
            date(2026, 8, 20),
        )


class TestBarChartGeometry:
    """RAP-03: server-rendered SVG geometry, no JS charting library."""

    def test_two_series_bar_chart_empty(self):
        assert report_services.two_series_bar_chart([]) == {"bars": [], "width": 760, "height": 180}

    def test_two_series_bar_chart_scales_to_max(self):
        series = [
            {"bucket": date(2026, 1, 1), "inflow": Decimal("100"), "outflow": Decimal("0")},
            {"bucket": date(2026, 1, 2), "inflow": Decimal("50"), "outflow": Decimal("25")},
        ]
        chart = report_services.two_series_bar_chart(series, width=100, height=100)
        assert len(chart["bars"]) == 2
        # Geometry is pre-formatted as plain '.'-decimal strings (not floats)
        # so Django's {{ }} can't re-localise them into invalid SVG like "3,0".
        # The tallest bar (100, the max across both series) reaches the top (y=0).
        assert chart["bars"][0]["inflow_y"] == "0.0"
        assert chart["bars"][0]["inflow_h"] == "100.0"

    def test_single_series_bar_chart_empty(self):
        assert report_services.single_series_bar_chart([]) == {
            "bars": [],
            "width": 760,
            "height": 140,
        }

    def test_dashboard_svg_uses_dot_decimals_not_locale_commas(self, client, django_user_model):
        """Regression: LANGUAGE_CODE="pl" makes Django's {{ }} render floats
        with a comma (x="3,0"), which is invalid SVG and silently fails to
        draw — geometry must reach the template as plain strings."""
        from django.contrib.auth.models import Permission

        paid_demo_order(client)
        user = django_user_model.objects.create_user("wykres", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_order"))
        client.login(username="wykres", password="x")

        page = client.get("/raporty/?tryb=demo").content.decode()
        assert "<rect" in page
        assert re.search(r'(x|y|width|height)="\d+,\d"', page) is None


class TestEventSalesCurve:
    def test_cumulative_revenue_and_tickets(self, client):
        event, order1 = paid_demo_order(client, quantity=2)  # +120
        _, order2 = paid_demo_order(client, event=event, quantity=1)  # +60

        curve = report_services.event_sales_curve(event, is_demo=True)
        assert len(curve) == 1  # both orders paid the same day -> one bucket
        assert curve[0]["revenue"] == Decimal("180.00")
        assert curve[0]["tickets"] == 3


class TestEventReportView:
    def test_requires_staff(self, client):
        event = make_event()
        assert client.get(f"/raporty/wydarzenie/{event.id}/").status_code == 302

    def test_shows_revenue_and_pool_breakdown(self, client, django_user_model):
        from django.contrib.auth.models import Permission

        event, order = paid_demo_order(client, quantity=2)
        user = django_user_model.objects.create_user("kierownik", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_order"))
        client.login(username="kierownik", password="x")

        page = client.get(f"/raporty/wydarzenie/{event.id}/?tryb=demo").content.decode()
        assert event.title in page
        assert "Udział pul biletowych" in page

    def test_linked_from_dashboard(self, client, django_user_model):
        from django.contrib.auth.models import Permission

        event, order = paid_demo_order(client)
        user = django_user_model.objects.create_user("kierownik2", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_order"))
        client.login(username="kierownik2", password="x")

        page = client.get("/raporty/?tryb=demo").content.decode()
        assert f"/raporty/wydarzenie/{event.id}/" in page


class TestExports:
    """RAP-06: XLSX alongside CSV; filenames carry the active period + mode."""

    def test_xlsx_endpoints_return_spreadsheet(self, client, django_user_model):
        from django.contrib.auth.models import Permission

        event, order = paid_demo_order(client)
        user = django_user_model.objects.create_user("ksiegowa", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_order"))
        client.login(username="ksiegowa", password="x")

        for url in ["/raporty/sprzedaz.xlsx", "/raporty/pule.xlsx", "/raporty/przeplywy.xlsx"]:
            resp = client.get(f"{url}?tryb=demo")
            assert resp.status_code == 200
            assert resp["Content-Type"] == (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            assert len(resp.content) > 0

    def test_export_filename_includes_period_and_mode(self, client, django_user_model):
        from django.contrib.auth.models import Permission

        user = django_user_model.objects.create_user("ksiegowa2", password="x", is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename="view_order"))
        client.login(username="ksiegowa2", password="x")

        year = timezone.now().year
        resp = client.get("/raporty/sprzedaz.csv?tryb=live&okres=ten_rok")
        assert f"sprzedaz_{year}_LIVE.csv" in resp["Content-Disposition"]

        quarter = (timezone.now().month - 1) // 3 + 1
        resp_quarter = client.get("/raporty/przeplywy.csv?tryb=demo&okres=ten_kwartal")
        assert f"przeplywy_{year}-Q{quarter}_DEMO.csv" in resp_quarter["Content-Disposition"]

        resp_all = client.get("/raporty/pule.csv?tryb=demo")
        assert "pule_wszystko_DEMO.csv" in resp_all["Content-Disposition"]
