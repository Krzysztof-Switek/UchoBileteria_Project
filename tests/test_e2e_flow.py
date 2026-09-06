"""
End-to-end lifecycle test: the 15 scenarios of spec section 25.3 executed
in order against the real HTTP endpoints (Django test client).
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounts.roles import setup_roles
from apps.checkin.services import ScanResult, check_in_by_short_code
from apps.events import services as event_services
from apps.events.models import CalendarOutbox, Event, EventStatus, TicketPool
from apps.orders import refunds
from apps.orders.models import Order, OrderStatus
from apps.tickets.models import EmailOutbox, TicketStatus
from apps.tickets.sending import send_pending_emails

pytestmark = pytest.mark.django_db


def test_full_event_lifecycle(client, django_user_model):
    now = timezone.now()
    setup_roles()

    # 1-2. Admin creates the event and its pools.
    admin = django_user_model.objects.create_superuser("szefowa", password="x")
    event = Event.objects.create(
        title="UCHO: Nocny koncert",
        slug="nocny-koncert",
        gates_open_at=now + timedelta(days=14, hours=-1),
        start_at=now + timedelta(days=14),
        end_at=now + timedelta(days=14, hours=5),
        sales_start_at=now - timedelta(hours=1),
        sales_end_at=now + timedelta(days=14),
        capacity_total=5,
        status=EventStatus.DRAFT,
        created_by=admin,
    )
    early = TicketPool.objects.create(
        event=event, name="Early Bird", price_gross=Decimal("40.00"),
        capacity=2,
    )
    TicketPool.objects.create(
        event=event, name="Regular", price_gross=Decimal("60.00"),
        capacity=5, sales_start_at=now + timedelta(days=7),
    )

    # 3. Admin publishes the event.
    event_services.publish_event(event, actor=admin)
    event.refresh_from_db()
    assert event.status == EventStatus.PUBLISHED

    # 4. Event appears on the website with the Early Bird price.
    page = client.get("/wydarzenia/nocny-koncert/").content.decode()
    assert "Nocny koncert" in page
    assert "Early Bird" in page

    # 5. Calendar sync task enqueued (the cron job pushes it to Google).
    assert CalendarOutbox.objects.filter(event=event, action="UPSERT").exists()

    # 6-7. Buyer purchases a ticket and pays in the demo cash desk;
    #      the payment webhook confirms it.
    client.post("/kup/nocny-koncert/", {"buyer_email": "fan@example.com", "quantity": 1})
    order = Order.objects.get()
    client.post(f"/kasa-demo/{order.id}/zaplac/")
    order.refresh_from_db()
    assert order.status == OrderStatus.PAID

    # 8. Ticket is issued.
    ticket = order.tickets.get()
    assert ticket.status == TicketStatus.ISSUED

    # 9. Ticket e-mail is generated and sent.
    assert EmailOutbox.objects.filter(order=order).exists()
    assert send_pending_emails() == 1

    # 10. QR/code scan checks the ticket in.
    result, _ = check_in_by_short_code(ticket.short_code, event)
    assert result == ScanResult.VALID_CHECKED_IN

    # 11. Second scan is rejected.
    result, _ = check_in_by_short_code(ticket.short_code, event)
    assert result == ScanResult.ALREADY_CHECKED_IN

    # 12-13. Admin refunds another (unused) order; its ticket cannot enter.
    client.post("/kup/nocny-koncert/", {"buyer_email": "drugi@example.com", "quantity": 1})
    order2 = Order.objects.latest("created_at")
    client.post(f"/kasa-demo/{order2.id}/zaplac/")
    order2.refresh_from_db()
    refunds.refund_order(order2, actor=admin)
    order2.refresh_from_db()
    assert order2.status == OrderStatus.REFUNDED
    ticket2 = order2.tickets.get()
    result, _ = check_in_by_short_code(ticket2.short_code, event)
    assert result == ScanResult.REFUNDED

    # 14. Early Bird sells out and Regular activates (price changes on page).
    # EB capacity 2: order1 holds one seat, the refunded seat is free again.
    client.post("/kup/nocny-koncert/", {"buyer_email": "trzeci@example.com", "quantity": 1})
    order3 = Order.objects.latest("created_at")
    client.post(f"/kasa-demo/{order3.id}/zaplac/")
    early.refresh_from_db()
    assert early.is_sold_out
    page = client.get("/wydarzenia/nocny-koncert/").content.decode()
    assert "Regular" in page  # next pool active before its start date

    # 15. Event sells out entirely (2 EB + 3 Regular = 5) and sales close.
    client.post("/kup/nocny-koncert/", {"buyer_email": "czwarty@example.com", "quantity": 3})
    order4 = Order.objects.latest("created_at")
    client.post(f"/kasa-demo/{order4.id}/zaplac/")
    event.refresh_from_db()
    assert event.is_sold_out
    page = client.get("/wydarzenia/nocny-koncert/").content.decode()
    assert "Wyprzedane" in page
    assert "Kup bilet" not in page

    # Door staff sanity: DOOR_STAFF group member can reach the scanner.
    door = django_user_model.objects.create_user("brama", password="x")
    door.groups.add(Group.objects.get(name="DOOR_STAFF"))
    client.login(username="brama", password="x")
    assert client.get(f"/wejscie/{event.id}/skaner/").status_code == 200

    # Reports reconcile: 4 paid orders (1 later refunded) x prices.
    from apps.reports.services import cash_flow_report, cash_flow_totals

    # 40 (o1) + 40 (o2, later refunded) + 40 (o3) + 3*60 (o4) = 300 in, 40 out.
    totals = cash_flow_totals(cash_flow_report(is_demo=True))
    assert totals["inflow"] == Decimal("300.00")
    assert totals["outflow"] == Decimal("40.00")
    assert totals["net"] == Decimal("260.00")
