"""Event financial report ('Raport sprzedaży' tile) and the emergency-list tile (05.10.2026)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.orders import refunds
from apps.orders.payments import mark_order_paid
from apps.orders.services import create_order
from apps.reports.finance import default_event, event_finance, split_gross
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


@pytest.fixture
def sold_event():
    event = make_event(title="Raport test")
    early = make_pool(event, name="Early", price_gross=Decimal("54.00"), capacity=10,
                      sales_start_at=timezone.now() - timedelta(days=5))
    order = create_order(event, "a@example.com", quantity=3)  # Early is the active pool
    mark_order_paid(order)
    return event, early, order


def test_split_gross_8_percent(settings):
    settings.TICKET_VAT_RATE = 8
    assert split_gross(Decimal("108.00")) == (Decimal("100.00"), Decimal("8.00"))


def test_totals_per_pool_and_partial_refund(sold_event):
    event, early, order = sold_event
    refunds.refund_tickets(order, list(order.tickets.all())[:1])
    data = event_finance(event, is_demo=True)
    pool = data["pools"][0]
    assert (pool["sold"], pool["refunded_tickets"]) == (2, 1)
    assert pool["received"] == Decimal("162.00") and pool["refunds"] == Decimal("54.00")
    assert pool["gross"] == Decimal("108.00") and pool["net"] + pool["vat"] == Decimal("108.00")
    assert pool["net"] < pool["gross"] and data["totals"]["refunded_tickets"] == 1


def test_default_event_is_closest_to_today():
    far = make_event(title="Daleko", start_at=timezone.now() + timedelta(days=60))
    near = make_event(title="Blisko", start_at=timezone.now() + timedelta(days=2))
    assert default_event([far, near]) == near


def test_report_page_and_pdf(admin_client, sold_event):
    event, *_ = sold_event
    html = admin_client.get(f"/raporty/finanse/?wydarzenie={event.pk}&tryb=demo").content.decode()
    assert "Raport finansowy" in html and "Early" in html and "Pobierz PDF" in html
    # amounts always in the order: net, VAT, gross; no order counts (05.10)
    assert html.index("Przychód netto") < html.index("VAT 8%") < html.index("Przychód brutto")
    assert "Zamówienia opłacone" not in html
    response = admin_client.get(f"/raporty/finanse/{event.pk}.pdf?tryb=demo")
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert "raport-finansowy-" in response["Content-Disposition"]


def test_dashboard_tiles_point_to_report_and_today_list(admin_client):
    html = admin_client.get("/admin/").content.decode()
    assert 'href="/raporty/finanse/"' in html and 'href="/wejscie/lista-awaryjna/"' in html


def test_emergency_tile_opens_todays_event(admin_client):
    make_event(title="Później", start_at=timezone.now() + timedelta(days=9))
    today = make_event(title="Dziś", start_at=timezone.now() + timedelta(hours=2))
    response = admin_client.get("/wejscie/lista-awaryjna/")
    assert response.status_code == 302
    assert response["Location"] == f"/wejscie/{today.pk}/lista-awaryjna/"
    html = admin_client.get(response["Location"]).content.decode()
    assert "Później" in html  # switcher to another event
