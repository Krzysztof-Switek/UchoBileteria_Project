"""
Sales and cash-flow reports.

Cash flow is computed from processed payment events (the same records the
webhook pipeline writes), so report totals always reconcile with what the
payment provider actually confirmed. Every query filters on is_demo so demo
and live money never mix.
"""

from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate

from apps.events.models import Event
from apps.orders.models import Order, OrderStatus, PaymentEvent, PaymentEventStatus
from apps.tickets.models import Ticket, TicketStatus

ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=12, decimal_places=2))


def event_sales_report(*, is_demo: bool) -> list[dict]:
    """Per-event sales summary: orders, tickets by status, money in/out."""
    rows = []
    events = Event.objects.order_by("start_at")
    for event in events:
        orders = Order.objects.filter(event=event, is_demo=is_demo)
        tickets = Ticket.objects.filter(event=event, is_demo=is_demo)
        money = orders.aggregate(
            paid=Coalesce(Sum("amount_gross", filter=Q(status=OrderStatus.PAID)), ZERO),
            refunded=Coalesce(
                Sum("amount_gross", filter=Q(status=OrderStatus.REFUNDED)), ZERO
            ),
        )
        ticket_counts = tickets.aggregate(
            issued=Count("pk", filter=Q(status=TicketStatus.ISSUED)),
            checked_in=Count("pk", filter=Q(status=TicketStatus.CHECKED_IN)),
            refunded=Count("pk", filter=Q(status=TicketStatus.REFUNDED)),
        )
        if not orders.exists() and not tickets.exists():
            continue
        rows.append(
            {
                "event": event,
                "orders_paid": orders.filter(status=OrderStatus.PAID).count(),
                "orders_refunded": orders.filter(status=OrderStatus.REFUNDED).count(),
                **ticket_counts,
                "revenue_paid": money["paid"],
                "revenue_refunded": money["refunded"],
                "revenue_net": money["paid"],  # paid orders still hold the money
            }
        )
    return rows


def cash_flow_report(*, is_demo: bool) -> list[dict]:
    """Daily inflows (payments) and outflows (refunds) from payment events."""
    events = (
        PaymentEvent.objects.filter(
            processing_status=PaymentEventStatus.PROCESSED,
            is_demo=is_demo,
            event_type__in=["payment.succeeded", "refund.succeeded"],
        )
        .annotate(day=TruncDate("processed_at"))
        .values("day")
        .annotate(
            inflow=Coalesce(Sum("amount", filter=Q(event_type="payment.succeeded")), ZERO),
            outflow=Coalesce(Sum("amount", filter=Q(event_type="refund.succeeded")), ZERO),
        )
        .order_by("day")
    )
    rows = []
    for entry in events:
        entry["net"] = entry["inflow"] - entry["outflow"]
        rows.append(entry)
    return rows


def cash_flow_totals(rows: list[dict]) -> dict:
    return {
        "inflow": sum((r["inflow"] for r in rows), Decimal("0")),
        "outflow": sum((r["outflow"] for r in rows), Decimal("0")),
        "net": sum((r["net"] for r in rows), Decimal("0")),
    }
