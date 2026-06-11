"""
Order creation with atomic capacity reservation, and order expiry.

Capacity safety relies on *conditional* atomic UPDATEs
(`UPDATE ... SET sold = sold + q WHERE sold <= capacity - q`), which are
race-free on both SQLite (single writer) and PostgreSQL (row locks).
A failed condition affects 0 rows and the whole transaction rolls back,
so the system can never oversell, no matter how many buyers race.
"""

from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone

from apps.auditlog.services import log_action
from apps.events.models import Event, EventStatus, TicketPool
from apps.events.services import SalesState, get_active_pool, get_sales_state

from .models import Order, OrderStatus, PaymentConfig, PaymentProviderKind

ORDER_TTL = timedelta(minutes=20)


class PurchaseError(Exception):
    """User-facing purchase problem (message is safe to display)."""


class CapacityError(PurchaseError):
    """Requested quantity is no longer available."""


def create_order(event: Event, buyer_email: str, quantity: int, now=None) -> Order:
    """Validate the purchase, atomically reserve capacity and create the order."""
    now = now or timezone.now()

    if event.status != EventStatus.PUBLISHED:
        raise PurchaseError("To wydarzenie nie jest dostępne w sprzedaży.")
    if not 1 <= quantity <= event.max_tickets_per_order:
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
        order = Order.objects.create(
            event=event,
            pool=pool,
            buyer_email=buyer_email,
            quantity=quantity,
            amount_gross=pool.price_gross * quantity,
            currency="DEMO" if is_demo else pool.currency,
            status=OrderStatus.CREATED,
            payment_provider=(
                PaymentProviderKind.DEMO if is_demo else PaymentProviderKind.STRIPE
            ),
            is_demo=is_demo,
            expires_at=now + ORDER_TTL,
        )

    log_action(
        "order.created",
        order,
        metadata={"quantity": quantity, "amount": str(order.amount_gross), "demo": is_demo},
    )
    return order


def _release_capacity(order: Order) -> None:
    """Give reserved seats back to the pool and the event."""
    TicketPool.objects.filter(pk=order.pool_id).update(
        sold_count=models.F("sold_count") - order.quantity
    )
    Event.objects.filter(pk=order.event_id).update(
        sold_total=models.F("sold_total") - order.quantity
    )


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
        _release_capacity(order)

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
        _release_capacity(order)

    order.refresh_from_db()
    log_action("order.cancelled", order)
    return True
