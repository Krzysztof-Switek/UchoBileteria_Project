"""
Pool activation and event sales-state logic (spec sections 5-6).

Statuses are computed at read time from dates, counters and manual overrides —
no scheduler involved, so there is no "cron didn't run" failure mode.
"""

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.auditlog.services import log_action

from .models import Event, EventStatus, PoolManualStatus, PoolSelloutAction, PoolStatus, TicketPool


class SalesState:
    """Effective sales state of a published event, for the public page."""

    NOT_STARTED = "NOT_STARTED"  # before event sales window
    ON_SALE = "ON_SALE"  # an active pool exists
    PAUSED = "PAUSED"  # no active pool now, but a future pool remains
    SOLD_OUT = "SOLD_OUT"  # event capacity reached
    CLOSED = "CLOSED"  # sales window over / no pool will ever activate


def pools_with_status(event: Event, now=None) -> list[tuple[TicketPool, str]]:
    """
    Return [(pool, computed_status), ...] ordered chronologically by
    sales_start_at — there's no separate priority field; time IS the order.

    Activation rule C: a pool becomes sellable when its start date has been
    reached OR the previous pool is exhausted (sold out or closed) *and*
    that previous pool's on_sellout is ACTIVATE_NEXT rather than PAUSE.
    Manual status always wins: FORCED_CLOSED -> CLOSED, FORCED_OPEN -> ACTIVE
    (unless sold out).
    """
    now = now or timezone.now()
    result: list[tuple[TicketPool, str]] = []
    # There is no previous pool for the first one, so only its start date
    # (or missing date) can activate it.
    previous_exhausted = False
    previous_cascades = True

    for pool in event.pools.order_by("sales_start_at"):
        if pool.manual_status == PoolManualStatus.FORCED_CLOSED:
            status = PoolStatus.CLOSED
        elif pool.is_sold_out:
            status = PoolStatus.SOLD_OUT
        elif pool.sales_end_at and now > pool.sales_end_at:
            status = PoolStatus.CLOSED
        elif pool.manual_status == PoolManualStatus.FORCED_OPEN:
            status = PoolStatus.ACTIVE
        else:
            if pool.sales_start_at is not None:
                date_trigger = now >= pool.sales_start_at
            else:
                # No date set: first pool opens immediately, later pools wait
                # for previous pools to sell out / close.
                date_trigger = not result
            cascade_trigger = previous_exhausted and previous_cascades
            status = PoolStatus.ACTIVE if (date_trigger or cascade_trigger) else PoolStatus.DRAFT

        result.append((pool, status))
        previous_exhausted = status in (PoolStatus.SOLD_OUT, PoolStatus.CLOSED)
        previous_cascades = pool.on_sellout == PoolSelloutAction.ACTIVATE_NEXT

    return result


def get_active_pool(event: Event, now=None) -> TicketPool | None:
    """The single sellable pool: earliest (by sales_start_at) among ACTIVE ones."""
    for pool, status in pools_with_status(event, now):
        if status == PoolStatus.ACTIVE:
            return pool
    return None


def get_next_pool(event: Event, now=None) -> TicketPool | None:
    """First pool that isn't sellable yet — used for the "kolejna pula" hint
    on the public event page (KUP-02)."""
    for pool, status in pools_with_status(event, now):
        if status == PoolStatus.DRAFT:
            return pool
    return None


def get_sales_state(event: Event, now=None) -> str:
    """Effective sales state for a PUBLISHED event (public page logic)."""
    now = now or timezone.now()
    if event.is_sold_out:
        return SalesState.SOLD_OUT
    if now < event.sales_start_at:
        return SalesState.NOT_STARTED
    if now > event.sales_end_at:
        return SalesState.CLOSED

    statuses = pools_with_status(event, now)
    if any(status == PoolStatus.ACTIVE for _, status in statuses):
        return SalesState.ON_SALE
    if any(status == PoolStatus.DRAFT for _, status in statuses):
        return SalesState.PAUSED  # a future pool may still activate
    return SalesState.CLOSED  # every pool sold out or closed, none left


def publish_event(event: Event, actor=None) -> Event:
    """DRAFT -> PUBLISHED with sanity checks. Audit-logged."""
    if event.status != EventStatus.DRAFT:
        raise ValidationError("Opublikować można tylko wydarzenie w stanie szkicu.")
    if event.capacity_total <= 0:
        raise ValidationError("Wydarzenie musi mieć dodatnią pojemność.")
    if not event.pools.exists():
        raise ValidationError("Wydarzenie musi mieć co najmniej jedną pulę biletów.")
    if event.sales_end_at <= event.sales_start_at:
        raise ValidationError("Koniec sprzedaży musi być po jej rozpoczęciu.")
    if event.gates_open_at is None:
        raise ValidationError("Wydarzenie musi mieć ustawiony czas otwarcia bramek.")
    if event.gates_open_at > event.start_at:
        raise ValidationError(
            "Bramki muszą się otworzyć przed startem koncertu (lub w tym samym momencie)."
        )
    if event.end_at is not None and event.end_at <= event.start_at:
        fmt = "%d.%m.%Y %H:%M"
        raise ValidationError(
            f"Koniec wydarzenia ({timezone.localtime(event.end_at):{fmt}}) musi być po "
            f"starcie koncertu ({timezone.localtime(event.start_at):{fmt}}) — popraw "
            "„Koniec” w kroku „Termin wydarzenia”."
        )
    if event.is_seated and not event.seats.filter(status="AVAILABLE").exists():
        raise ValidationError(
            "Wydarzenie z miejscami numerowanymi musi mieć przypięty plan sali z wolnymi "
            "miejscami (przycisk „Plan sali”)."
        )

    event.status = EventStatus.PUBLISHED
    event.updated_by = actor
    event.save(update_fields=["status", "updated_by", "updated_at"])
    log_action("event.published", event, actor=actor)

    # Non-blocking: the calendar sync runs from cron and may fail safely.
    from .calendar import enqueue_calendar_sync
    from .models import CalendarAction

    enqueue_calendar_sync(event, CalendarAction.UPSERT)
    return event


def cancel_event(event: Event, actor=None) -> Event:
    """PUBLISHED/DRAFT -> CANCELLED. Mass refunds are handled separately."""
    if event.status in (EventStatus.CANCELLED, EventStatus.FINISHED):
        raise ValidationError("Tego wydarzenia nie można już odwołać.")
    event.status = EventStatus.CANCELLED
    event.updated_by = actor
    event.save(update_fields=["status", "updated_by", "updated_at"])
    log_action("event.cancelled", event, actor=actor)

    from .calendar import enqueue_calendar_sync
    from .models import CalendarAction

    enqueue_calendar_sync(event, CalendarAction.CANCEL)
    return event


def set_pool_manual_status(pool: TicketPool, manual_status: str, actor=None) -> TicketPool:
    """Admin override: open / close / back to automatic. Audit-logged."""
    if manual_status not in PoolManualStatus.values:
        raise ValidationError(f"Nieznany status ręczny: {manual_status}")
    pool.manual_status = manual_status
    pool.save(update_fields=["manual_status", "updated_at"])
    log_action("pool.manual_status_set", pool, actor=actor, metadata={"status": manual_status})
    return pool


def copy_event_for_new_date(event: Event, new_start, actor=None) -> Event:
    """Postponement (06.10.2026): the same show on a new date, made by clicking
    an empty day in the cancel screen's calendar. Copies the description,
    times (shifted by the same amount), pools (nothing sold) and the seat plan;
    published when the original was, so buyers can keep their tickets there."""
    from django.utils.text import slugify

    from .models import online_sales_cutoff

    shift = new_start - event.start_at
    base = slugify(f"{event.slug}-{timezone.localtime(new_start):%Y-%m-%d}")[:45]
    slug, n = base, 2
    while Event.objects.filter(slug=slug).exists():
        slug, n = f"{base}-{n}", n + 1
    now = timezone.now()
    copy = Event.objects.create(
        title=event.title, slug=slug, description=event.description,
        venue_name=event.venue_name, venue_address=event.venue_address,
        gates_open_at=event.gates_open_at + shift if event.gates_open_at else None,
        start_at=new_start,
        end_at=event.end_at + shift if event.end_at else None,
        sales_start_at=now, sales_end_at=online_sales_cutoff(new_start),
        capacity_total=max(sum(p.capacity for p in event.pools.all()), 1),
        status=EventStatus.DRAFT, max_tickets_per_order=event.max_tickets_per_order,
        created_by=actor, updated_by=actor,
    )
    for pool in event.pools.order_by("pk"):
        TicketPool.objects.create(
            event=copy, name=pool.name, price_gross=pool.price_gross, currency=pool.currency,
            capacity=pool.capacity, on_sellout=pool.on_sellout, sales_start_at=now,
            sales_end_at=online_sales_cutoff(new_start),
        )
    seat_map = getattr(event, "seat_map", None)
    if seat_map is not None and seat_map.source_layout_id:
        from apps.seating.services import attach_layout

        attach_layout(copy, seat_map.source_layout, actor=actor)
    log_action("event.copied_for_new_date", copy, actor=actor,
               metadata={"source_id": event.pk, "start": new_start.isoformat()})
    if event.status == EventStatus.PUBLISHED:
        publish_event(copy, actor=actor)
    copy.refresh_from_db()
    return copy
