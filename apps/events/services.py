"""
Pool activation and event sales-state logic (spec sections 5-6).

Statuses are computed at read time from dates, counters and manual overrides —
no scheduler involved, so there is no "cron didn't run" failure mode.
"""

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.auditlog.services import log_action

from .models import Event, EventStatus, PoolManualStatus, PoolStatus, TicketPool


class SalesState:
    """Effective sales state of a published event, for the public page."""

    NOT_STARTED = "NOT_STARTED"  # before event sales window
    ON_SALE = "ON_SALE"  # an active pool exists
    PAUSED = "PAUSED"  # no active pool now, but a future pool remains
    SOLD_OUT = "SOLD_OUT"  # event capacity reached
    CLOSED = "CLOSED"  # sales window over / no pool will ever activate


def pools_with_status(event: Event, now=None) -> list[tuple[TicketPool, str]]:
    """
    Return [(pool, computed_status), ...] ordered by priority.

    Activation rule C: a pool becomes sellable when its start date has been
    reached OR every previous pool is exhausted (sold out or closed).
    Manual status always wins: FORCED_CLOSED -> CLOSED, FORCED_OPEN -> ACTIVE
    (unless sold out).
    """
    now = now or timezone.now()
    result: list[tuple[TicketPool, str]] = []
    # There is no previous pool for the first one, so only its start date
    # (or missing date) can activate it.
    previous_exhausted = False

    for pool in event.pools.order_by("priority"):
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
            status = PoolStatus.ACTIVE if (date_trigger or previous_exhausted) else PoolStatus.DRAFT

        result.append((pool, status))
        previous_exhausted = status in (PoolStatus.SOLD_OUT, PoolStatus.CLOSED)

    return result


def get_active_pool(event: Event, now=None) -> TicketPool | None:
    """The single sellable pool: lowest priority among ACTIVE ones."""
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
    if event.end_at <= event.start_at:
        raise ValidationError("Koniec wydarzenia musi być po jego rozpoczęciu.")

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
