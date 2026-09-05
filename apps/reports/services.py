"""
Sales and cash-flow reports.

Cash flow is computed from processed payment events (the same records the
webhook pipeline writes), so report totals always reconcile with what the
payment provider actually confirmed. Every query filters on is_demo so demo
and live money never mix.
"""

import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Avg, Count, DecimalField, Q, QuerySet, Sum, Value
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from apps.events.models import Event, TicketPool
from apps.orders.models import Order, OrderStatus, PaymentEvent, PaymentEventStatus
from apps.tickets.models import Ticket, TicketStatus

ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=12, decimal_places=2))

# RAP-01: one-click report periods. Quarters/years follow the calendar year —
# revisit if the club ever adopts a different fiscal year (open decision #2
# in docs/Hendouts TODOs/21.08_audyt_ux_PLAN_TO_DO.md).
PERIOD_CHOICES = [
    ("dzis", "Dziś"),
    ("7dni", "Ostatnie 7 dni"),
    ("ten_miesiac", "Ten miesiąc"),
    ("poprzedni_miesiac", "Poprzedni miesiąc"),
    ("ten_kwartal", "Ten kwartał"),
    ("poprzedni_kwartal", "Poprzedni kwartał"),
    ("ten_rok", "Ten rok"),
]


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = date(year, start_month, 1)
    end = date(year, end_month, calendar.monthrange(year, end_month)[1])
    return start, end


def resolve_period(okres: str, today: date | None = None) -> tuple[date, date] | None:
    """Predefined period name -> (date_from, date_to), inclusive. None if unknown
    (falls back to the custom ?od=&do= range in the caller)."""
    today = today or timezone.now().date()
    if okres == "dzis":
        return today, today
    if okres == "7dni":
        return today - timedelta(days=6), today
    if okres == "ten_miesiac":
        return today.replace(day=1), today
    if okres == "poprzedni_miesiac":
        last_day_prev_month = today.replace(day=1) - timedelta(days=1)
        return last_day_prev_month.replace(day=1), last_day_prev_month
    if okres == "ten_kwartal":
        quarter = (today.month - 1) // 3 + 1
        start, _ = _quarter_bounds(today.year, quarter)
        return start, today
    if okres == "poprzedni_kwartal":
        quarter = (today.month - 1) // 3 + 1
        year = today.year
        quarter -= 1
        if quarter == 0:
            quarter, year = 4, year - 1
        return _quarter_bounds(year, quarter)
    if okres == "ten_rok":
        return date(today.year, 1, 1), today
    return None


def previous_period(date_from: date, date_to: date) -> tuple[date, date]:
    """RAP-02: the same-length window immediately preceding date_from."""
    length = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    prev_from = prev_to - timedelta(days=length - 1)
    return prev_from, prev_to


def percent_change(current, previous) -> float | None:
    """None when there's nothing to compare against (previous period empty) —
    an infinite/undefined percentage isn't meaningful to show."""
    if not previous:
        return None
    return float((Decimal(current) - Decimal(previous)) / Decimal(previous) * 100)


def period_kpis(*, is_demo: bool, date_from: date | None, date_to: date | None) -> dict:
    """Period KPIs anchored on when money actually moved (same semantics as
    cash_flow_report, i.e. PaymentEvent.processed_at) — not event dates, so
    this matches "ile sprzedaliśmy w tym kwartale" regardless of when the
    events themselves happen."""
    flows = cash_flow_report(is_demo=is_demo, date_from=date_from, date_to=date_to)
    flow_totals = cash_flow_totals(flows)

    orders = Order.objects.filter(is_demo=is_demo, status=OrderStatus.PAID)
    if date_from:
        orders = orders.filter(paid_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(paid_at__date__lte=date_to)
    order_agg = orders.aggregate(
        count=Count("pk"),
        tickets=Coalesce(Sum("quantity"), 0),
        avg_value=Coalesce(Avg("amount_gross"), ZERO),
    )
    return {
        "revenue_net": flow_totals["net"],
        "tickets_sold": order_agg["tickets"],
        "avg_order_value": order_agg["avg_value"],
        "refunds": flow_totals["outflow"],
    }


def _order_money_totals(orders: QuerySet) -> dict:
    """Paid/refunded totals for a queryset of Order rows."""
    return orders.aggregate(
        paid=Coalesce(Sum("amount_gross", filter=Q(status=OrderStatus.PAID)), ZERO),
        refunded=Coalesce(
            Sum("amount_gross", filter=Q(status=OrderStatus.REFUNDED)), ZERO
        ),
    )


def _ticket_status_counts(tickets: QuerySet) -> dict:
    return tickets.aggregate(
        issued=Count("pk", filter=Q(status=TicketStatus.ISSUED)),
        checked_in=Count("pk", filter=Q(status=TicketStatus.CHECKED_IN)),
        refunded=Count("pk", filter=Q(status=TicketStatus.REFUNDED)),
    )


def event_revenue_summary(event: Event, *, is_demo: bool) -> dict:
    """Paid/refunded revenue for a single event (reused by the admin panel)."""
    return _order_money_totals(Order.objects.filter(event=event, is_demo=is_demo))


def event_ticket_counts(event: Event, *, is_demo: bool) -> dict:
    """Ticket status breakdown for a single event (RAP-04)."""
    return _ticket_status_counts(Ticket.objects.filter(event=event, is_demo=is_demo))


def event_sales_report(
    *, is_demo: bool, date_from: date | None = None, date_to: date | None = None
) -> list[dict]:
    """Per-event sales summary: orders, tickets by status, money in/out."""
    rows = []
    events = Event.objects.order_by("start_at")
    if date_from:
        events = events.filter(start_at__date__gte=date_from)
    if date_to:
        events = events.filter(start_at__date__lte=date_to)
    for event in events:
        orders = Order.objects.filter(event=event, is_demo=is_demo)
        tickets = Ticket.objects.filter(event=event, is_demo=is_demo)
        money = _order_money_totals(orders)
        ticket_counts = _ticket_status_counts(tickets)
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


def pool_sales_report(
    *,
    is_demo: bool,
    event: Event | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """Per-pool sales summary, mirroring event_sales_report but grouped by TicketPool."""
    rows = []
    pools = TicketPool.objects.select_related("event").order_by(
        "event__start_at", "priority"
    )
    if event is not None:
        pools = pools.filter(event=event)
    if date_from:
        pools = pools.filter(event__start_at__date__gte=date_from)
    if date_to:
        pools = pools.filter(event__start_at__date__lte=date_to)
    for pool in pools:
        orders = pool.orders.filter(is_demo=is_demo)
        tickets = pool.tickets.filter(is_demo=is_demo)
        money = _order_money_totals(orders)
        ticket_counts = _ticket_status_counts(tickets)
        if not orders.exists() and not tickets.exists():
            continue
        rows.append(
            {
                "pool": pool,
                "event": pool.event,
                "orders_paid": orders.filter(status=OrderStatus.PAID).count(),
                "orders_refunded": orders.filter(status=OrderStatus.REFUNDED).count(),
                **ticket_counts,
                "revenue_paid": money["paid"],
                "revenue_refunded": money["refunded"],
            }
        )
    return rows


def cash_flow_report(
    *, is_demo: bool, date_from: date | None = None, date_to: date | None = None
) -> list[dict]:
    """Daily inflows (payments) and outflows (refunds) from payment events."""
    events = PaymentEvent.objects.filter(
        processing_status=PaymentEventStatus.PROCESSED,
        is_demo=is_demo,
        event_type__in=["payment.succeeded", "refund.succeeded"],
    )
    if date_from:
        events = events.filter(processed_at__date__gte=date_from)
    if date_to:
        events = events.filter(processed_at__date__lte=date_to)
    events = (
        events.annotate(day=TruncDate("processed_at"))
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


def sales_totals(rows: list[dict]) -> dict:
    """Column sums for event_sales_report/pool_sales_report rows (RAP-05:
    footer totals) — both share the same aggregate keys."""
    return {
        "orders_paid": sum(r["orders_paid"] for r in rows),
        "orders_refunded": sum(r["orders_refunded"] for r in rows),
        "issued": sum(r["issued"] for r in rows),
        "checked_in": sum(r["checked_in"] for r in rows),
        "refunded": sum(r["refunded"] for r in rows),
        "revenue_paid": sum((r["revenue_paid"] for r in rows), Decimal("0")),
        "revenue_refunded": sum((r["revenue_refunded"] for r in rows), Decimal("0")),
    }


def cash_flow_totals(rows: list[dict]) -> dict:
    return {
        "inflow": sum((r["inflow"] for r in rows), Decimal("0")),
        "outflow": sum((r["outflow"] for r in rows), Decimal("0")),
        "net": sum((r["net"] for r in rows), Decimal("0")),
    }


def cash_flow_chart_series(
    *, is_demo: bool, date_from: date | None, date_to: date | None
) -> list[dict]:
    """RAP-03: same data as cash_flow_report, bucketed by day/week/month
    depending on the span so a year-long period doesn't render 365 bars."""
    events = PaymentEvent.objects.filter(
        processing_status=PaymentEventStatus.PROCESSED,
        is_demo=is_demo,
        event_type__in=["payment.succeeded", "refund.succeeded"],
    )
    if date_from:
        events = events.filter(processed_at__date__gte=date_from)
    if date_to:
        events = events.filter(processed_at__date__lte=date_to)

    span_days = (date_to - date_from).days if date_from and date_to else 9999
    trunc = TruncDate("processed_at") if span_days <= 31 else (
        TruncWeek("processed_at") if span_days <= 180 else TruncMonth("processed_at")
    )

    rows = (
        events.annotate(bucket=trunc)
        .values("bucket")
        .annotate(
            inflow=Coalesce(Sum("amount", filter=Q(event_type="payment.succeeded")), ZERO),
            outflow=Coalesce(Sum("amount", filter=Q(event_type="refund.succeeded")), ZERO),
        )
        .order_by("bucket")
    )
    return list(rows)


def _svg_num(value: float) -> str:
    """Plain '.'-decimal string for SVG attributes. Django's {{ }} would
    otherwise localise floats with a comma (LANGUAGE_CODE="pl"), which SVG
    can't parse — x="3,0" silently fails to render instead of erroring."""
    return f"{value:.1f}"


def two_series_bar_chart(
    series: list[dict],
    *,
    width: int = 760,
    height: int = 180,
    gap: int = 3,
) -> dict:
    """SVG geometry (RAP-03) for a paired inflow/outflow bar chart, computed
    server-side — no JS charting library, prints like any other content."""
    if not series:
        return {"bars": [], "width": width, "height": height}
    max_value = max(
        [row["inflow"] for row in series] + [row["outflow"] for row in series],
        default=Decimal("0"),
    ) or Decimal("1")
    slot_width = width / len(series)
    bar_width = max((slot_width - gap * 3) / 2, 1)
    bars = []
    for i, row in enumerate(series):
        slot_x = i * slot_width + gap
        inflow_h = float(row["inflow"] / max_value) * height
        outflow_h = float(row["outflow"] / max_value) * height
        bars.append(
            {
                "label": row["bucket"],
                "inflow": row["inflow"],
                "outflow": row["outflow"],
                "inflow_x": _svg_num(slot_x),
                "inflow_y": _svg_num(height - inflow_h),
                "inflow_h": _svg_num(inflow_h),
                "outflow_x": _svg_num(slot_x + bar_width + gap),
                "outflow_y": _svg_num(height - outflow_h),
                "outflow_h": _svg_num(outflow_h),
                "bar_width": _svg_num(bar_width),
            }
        )
    return {"bars": bars, "width": width, "height": height}


def event_sales_curve(event: Event, *, is_demo: bool) -> list[dict]:
    """RAP-04: cumulative paid revenue/tickets by day, from the first order
    to the event's start — "krzywa sprzedaży"."""
    rows = (
        Order.objects.filter(event=event, is_demo=is_demo, status=OrderStatus.PAID)
        .annotate(day=TruncDate("paid_at"))
        .values("day")
        .annotate(revenue=Sum("amount_gross"), tickets=Sum("quantity"))
        .order_by("day")
    )
    cumulative_revenue = Decimal("0")
    cumulative_tickets = 0
    curve = []
    for row in rows:
        cumulative_revenue += row["revenue"] or Decimal("0")
        cumulative_tickets += row["tickets"] or 0
        curve.append(
            {
                "day": row["day"],
                "revenue": cumulative_revenue,
                "tickets": cumulative_tickets,
            }
        )
    return curve


def single_series_bar_chart(
    values: list[Decimal], *, width: int = 760, height: int = 140, gap: int = 3
) -> dict:
    """SVG geometry for a single-series bar chart (RAP-04's sales curve)."""
    if not values:
        return {"bars": [], "width": width, "height": height}
    max_value = max(values) or Decimal("1")
    slot_width = width / len(values)
    bar_width = max(slot_width - gap * 2, 1)
    bars = []
    for i, value in enumerate(values):
        h = float(value / max_value) * height
        bars.append(
            {
                "x": _svg_num(i * slot_width + gap),
                "y": _svg_num(height - h),
                "h": _svg_num(h),
                "width": _svg_num(bar_width),
                "value": value,
            }
        )
    return {"bars": bars, "width": width, "height": height}
