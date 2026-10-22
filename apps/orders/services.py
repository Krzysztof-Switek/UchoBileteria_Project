"""
Order creation with atomic capacity reservation, and order expiry.

Capacity safety relies on *conditional* atomic UPDATEs
(`UPDATE ... SET sold = sold + q WHERE sold <= capacity - q`), which are
race-free on both SQLite (single writer) and PostgreSQL (row locks).
A failed condition affects 0 rows and the whole transaction rolls back,
so the system can never oversell, no matter how many buyers race.

Seated events (apps.seating) add one more conditional UPDATE in the same
transaction: `EventSeat ... WHERE status = AVAILABLE` must change exactly as many
rows as seats were requested, otherwise everything rolls back — a seat can
never be sold twice.
"""

from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

from apps.auditlog.services import log_action
from apps.events.models import Event, EventStatus, TicketPool
from apps.events.services import SalesState, get_active_pool, get_sales_state
from apps.seating.layout import zone_price
from apps.seating.models import EventSeat, SeatStatus

from .models import Order, OrderStatus, PaymentConfig, PaymentProviderKind

ORDER_TTL = timedelta(minutes=20)


class PurchaseError(Exception):
    """User-facing purchase problem (message is safe to display)."""


class CapacityError(PurchaseError):
    """Requested quantity is no longer available."""


class SeatConflictError(CapacityError):
    """Some of the chosen seats were taken by someone else in the meantime."""

    def __init__(self, taken: list[EventSeat], still_free: list[str]):
        self.taken_keys = [s.seat_key for s in taken]
        self.taken_labels = [s.label for s in taken]
        self.still_free = still_free
        what = ", ".join(self.taken_labels) or "Część wybranych miejsc"
        verb = "zostało właśnie zajęte" if len(taken) == 1 else "zostały właśnie zajęte"
        super().__init__(f"{what} — {verb}. Wybierz inne miejsca na mapie.")


def _load_seats(event: Event, seat_keys) -> list[EventSeat]:
    """The chosen seats of this event, validated (existing, no duplicates, rules)."""
    from apps.seating.services import selection_problem

    keys = [k for k in (seat_keys or []) if isinstance(k, str) and k]
    if len(set(keys)) != len(keys):
        raise PurchaseError("To samo miejsce wybrano dwa razy.")
    seats = list(EventSeat.objects.filter(event=event, seat_key__in=keys))
    if not keys or len(seats) != len(keys):
        raise PurchaseError("Wybierz miejsca na mapie sali.")
    problem = selection_problem(event, seats)
    if problem:
        raise PurchaseError(problem)
    return sorted(seats, key=lambda s: s.sort_index)


def _hold_seats(event: Event, seats: list[EventSeat], order: Order, status=SeatStatus.HELD):
    """Conditional UPDATE: every requested seat AVAILABLE -> HELD/SOLD, or raise
    SeatConflictError (the caller's transaction then rolls everything back)."""
    keys = [s.seat_key for s in seats]
    taken_now = EventSeat.objects.filter(
        event=event, seat_key__in=keys, status=SeatStatus.AVAILABLE
    ).update(status=status, order=order)
    if taken_now != len(keys):
        lost = list(
            EventSeat.objects.filter(event=event, seat_key__in=keys)
            .exclude(order=order)
            .exclude(status=SeatStatus.AVAILABLE)
        )
        lost_keys = {s.seat_key for s in lost}
        raise SeatConflictError(lost, [k for k in keys if k not in lost_keys])


def create_order(
    event: Event, buyer_email: str, quantity: int | None = None, now=None, seat_keys=None,
    terms_version: str = "",
) -> Order:
    """Validate the purchase, atomically reserve capacity (and seats) and create the order."""
    now = now or timezone.now()

    if event.status != EventStatus.PUBLISHED:
        raise PurchaseError("To wydarzenie nie jest dostępne w sprzedaży.")
    seats: list[EventSeat] = []
    if event.is_seated:
        seats = _load_seats(event, seat_keys)
        quantity = len(seats)
    elif quantity is None or not 1 <= quantity <= event.max_tickets_per_order:
        raise PurchaseError(
            f"Można kupić od 1 do {event.max_tickets_per_order} biletów w jednym zamówieniu."
        )
    state = get_sales_state(event, now)
    if state != SalesState.ON_SALE:
        raise PurchaseError("Sprzedaż biletów na to wydarzenie jest obecnie niedostępna.")

    with transaction.atomic():
        pool = get_active_pool(event, now)
        if pool is None:
            raise PurchaseError("Żadna pula biletów nie jest obecnie aktywna.")

        reserved_pool = TicketPool.objects.filter(
            pk=pool.pk,
            sold_count__lte=models.F("capacity") - quantity,
        ).update(sold_count=models.F("sold_count") + quantity)
        if not reserved_pool:
            raise CapacityError("Zabrakło biletów w aktualnej puli. Spróbuj ponownie.")

        reserved_event = Event.objects.filter(
            pk=event.pk,
            sold_total__lte=models.F("capacity_total") - quantity,
        ).update(sold_total=models.F("sold_total") + quantity)
        if not reserved_event:
            # raising rolls back the pool reservation above
            raise CapacityError("Zabrakło wolnych miejsc na to wydarzenie.")

        is_demo = PaymentConfig.is_demo_mode()
        # price zones: every seat = active pool price × its zone's percent
        prices = [zone_price(pool.price_gross, s.price_percent) for s in seats]
        order = Order.objects.create(
            event=event,
            pool=pool,
            buyer_email=buyer_email,
            quantity=quantity,
            amount_gross=sum(prices) if seats else pool.price_gross * quantity,
            seat_prices=[str(p) for p in prices],
            currency="DEMO" if is_demo else pool.currency,
            status=OrderStatus.CREATED,
            payment_provider=(
                PaymentProviderKind.DEMO if is_demo else PaymentProviderKind.STRIPE
            ),
            is_demo=is_demo,
            expires_at=now + ORDER_TTL,
            seat_keys=[s.seat_key for s in seats],
            seat_labels=[s.label for s in seats],
            terms_version=terms_version,
            terms_accepted_at=now if terms_version else None,
        )
        if seats:
            _hold_seats(event, seats, order)

    metadata = {"quantity": quantity, "amount": str(order.amount_gross), "demo": is_demo}
    if seats:
        metadata["seats"] = order.seat_labels
    log_action("order.created", order, metadata=metadata)
    return order


def release_capacity(order: Order) -> None:
    """
    Give reserved/sold capacity back: pool and event counters, plus the order's
    seats on a seated event. The single place every exit path (expiry, buyer
    cancel, failed payment, refund) goes through — call it inside the
    transaction that claimed the status change.
    """
    held = order.quantity - order.refunded_quantity  # partial refunds already gave theirs back
    TicketPool.objects.filter(pk=order.pool_id).update(sold_count=models.F("sold_count") - held)
    Event.objects.filter(pk=order.event_id).update(sold_total=models.F("sold_total") - held)
    EventSeat.objects.filter(
        order=order, status__in=[SeatStatus.HELD, SeatStatus.SOLD]
    ).update(status=SeatStatus.AVAILABLE, order=None)


def change_pending_order_seats(order: Order, seat_keys, now=None) -> Order:
    """
    Buyer changes their mind before paying: swap the held seats for others,
    same number and same price zones (so the amount and an open payment
    session stay valid). Seats present in both selections are never released
    in between.
    """
    now = now or timezone.now()
    if order.status not in (OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING) or (
        order.expires_at and order.expires_at <= now
    ):
        raise PurchaseError("Tego zamówienia nie można już zmienić.")
    event = order.event
    seats = _load_seats(event, seat_keys)
    if len(seats) != order.quantity:
        raise PurchaseError(
            f"Wybierz tyle samo miejsc, ile jest w zamówieniu ({order.quantity})."
        )
    old_seats = list(EventSeat.objects.filter(event=event, seat_key__in=order.seat_keys))
    if sorted(s.price_percent for s in old_seats) != sorted(s.price_percent for s in seats):
        raise PurchaseError(
            "Nowe miejsca muszą być w tych samych strefach cenowych co obecne "
            "(cena zamówienia się nie zmienia). Inną strefę kupisz w nowym zamówieniu."
        )
    # each new seat takes the price paid for an old seat of the same zone
    paid = {}
    for key, price in zip(order.seat_keys, order.seat_prices or [], strict=False):
        percent = next((s.price_percent for s in old_seats if s.seat_key == key), 100)
        paid.setdefault(percent, []).append(price)
    new_prices = [paid[s.price_percent].pop(0) if paid.get(s.price_percent) else ""
                  for s in seats]
    old_keys = set(order.seat_keys)
    new_seats = [s for s in seats if s.seat_key not in old_keys]
    dropped = old_keys - {s.seat_key for s in seats}
    old_labels = list(order.seat_labels)
    with transaction.atomic():
        claimed = Order.objects.filter(
            pk=order.pk, status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]
        ).update(seat_keys=[s.seat_key for s in seats], seat_labels=[s.label for s in seats],
                 seat_prices=new_prices)
        if not claimed:
            raise PurchaseError("Tego zamówienia nie można już zmienić.")
        if new_seats:
            _hold_seats(event, new_seats, order)
        EventSeat.objects.filter(
            order=order, seat_key__in=dropped, status=SeatStatus.HELD
        ).update(status=SeatStatus.AVAILABLE, order=None)
    order.refresh_from_db()
    log_action("order.seats_changed", order, metadata={"from": old_labels, "to": order.seat_labels})
    return order


def expire_order(order: Order) -> bool:
    """
    Expire a single unpaid order and release its capacity.

    The conditional status UPDATE claims the order exactly once, so a racing
    payment webhook (or a second cron run) can never double-release capacity.
    """
    with transaction.atomic():
        claimed = Order.objects.filter(
            pk=order.pk,
            status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING],
        ).update(status=OrderStatus.EXPIRED)
        if not claimed:
            return False
        release_capacity(order)

    order.refresh_from_db()
    log_action("order.expired", order)
    return True


def expire_stale_orders(now=None) -> int:
    """Expire every unpaid order past its deadline. Returns how many."""
    now = now or timezone.now()
    stale = Order.objects.filter(
        status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING],
        expires_at__lt=now,
    )
    count = 0
    for order in stale:
        if expire_order(order):
            count += 1
    return count


def cancel_unpaid_order(order: Order) -> bool:
    """Buyer abandoned checkout: cancel and release capacity (idempotent)."""
    with transaction.atomic():
        claimed = Order.objects.filter(
            pk=order.pk,
            status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING],
        ).update(status=OrderStatus.CANCELLED)
        if not claimed:
            return False
        release_capacity(order)

    order.refresh_from_db()
    log_action("order.cancelled", order)
    return True
