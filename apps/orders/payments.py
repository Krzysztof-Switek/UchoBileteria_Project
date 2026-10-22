"""
Provider-agnostic webhook processing (spec section 13.1).

Idempotency: every webhook is recorded in `payment_events` with a unique
(provider, provider_event_id) pair *before* any side effect. A duplicate
insert means the event was already handled and the call becomes a no-op.
"""

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.auditlog.services import log_action
from apps.events.models import Event, TicketPool
from apps.seating.models import EventSeat, SeatStatus

from .models import (
    Order,
    OrderRefund,
    OrderStatus,
    PaymentEvent,
    PaymentEventStatus,
    RefundStatus,
)
from .services import release_capacity


class SignatureError(Exception):
    """Webhook signature verification failed."""


def process_webhook(provider: str, payload: dict, payload_hash: str = "") -> PaymentEvent:
    """Record and process a verified webhook payload. Safe to call twice."""
    order = Order.objects.filter(id=payload.get("order_id")).first()

    try:
        with transaction.atomic():
            record = PaymentEvent.objects.create(
                provider=provider,
                provider_event_id=payload["id"],
                event_type=payload.get("type", ""),
                order=order,
                amount=payload.get("amount") or None,
                is_demo=order.is_demo if order else provider == "DEMO",
                payload_hash=payload_hash,
            )
    except IntegrityError:
        # Same provider event already recorded -> duplicate delivery, no-op.
        return PaymentEvent.objects.get(
            provider=provider, provider_event_id=payload["id"]
        )

    event_type = payload.get("type", "")
    handlers = {
        "payment.succeeded": mark_order_paid,
        "payment.failed": mark_order_failed,
        "refund.succeeded": mark_order_refunded,
    }
    if event_type not in handlers:
        _finish(record, PaymentEventStatus.IGNORED)
    elif order is None:
        # A money-moving event we cannot match to any order — needs a human.
        _finish(record, PaymentEventStatus.ERROR)
    elif event_type == "refund.succeeded" and payload.get("refund_id"):
        ok = apply_partial_refund(order, payload["refund_id"])
        _finish(record, PaymentEventStatus.PROCESSED if ok else PaymentEventStatus.IGNORED)
    else:
        ok = handlers[event_type](order)
        _finish(record, PaymentEventStatus.PROCESSED if ok else PaymentEventStatus.IGNORED)
    return record


def _finish(record: PaymentEvent, status: str) -> None:
    record.processing_status = status
    record.processed_at = timezone.now()
    record.save(update_fields=["processing_status", "processed_at"])


def mark_order_paid(order: Order) -> bool:
    """
    PAYMENT_PENDING/CREATED -> PAID. For an EXPIRED order (late webhook) try to
    re-reserve capacity; when the event meanwhile sold out the order stays
    EXPIRED and the caller's payment event is IGNORED (admin sees it and the
    refund flow returns the money).
    """
    now = timezone.now()
    claimed = Order.objects.filter(
        pk=order.pk, status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]
    ).update(status=OrderStatus.PAID, paid_at=now)
    if claimed:
        EventSeat.objects.filter(order=order, status=SeatStatus.HELD).update(
            status=SeatStatus.SOLD
        )
        order.refresh_from_db()
        log_action("order.paid", order, metadata={"amount": str(order.amount_gross)})
        _issue_tickets_if_configured(order)
        return True

    order.refresh_from_db()
    if order.status == OrderStatus.EXPIRED:
        if _try_rereserve(order):
            Order.objects.filter(pk=order.pk, status=OrderStatus.EXPIRED).update(
                status=OrderStatus.PAID, paid_at=now
            )
            order.refresh_from_db()
            log_action("order.paid_late", order, metadata={"rereserved": True})
            _issue_tickets_if_configured(order)
            return True
        log_action("order.late_payment_no_capacity", order)
        return False
    return False  # already PAID / REFUNDED / CANCELLED -> nothing to do


def _try_rereserve(order: Order) -> bool:
    """Atomically take the capacity back for a late-paid expired order — on a
    seated event the very same seats, or nothing (someone else may own them now)."""
    with transaction.atomic():
        pool_ok = TicketPool.objects.filter(
            pk=order.pool_id, sold_count__lte=models.F("capacity") - order.quantity
        ).update(sold_count=models.F("sold_count") + order.quantity)
        if not pool_ok:
            return False
        event_ok = Event.objects.filter(
            pk=order.event_id, sold_total__lte=models.F("capacity_total") - order.quantity
        ).update(sold_total=models.F("sold_total") + order.quantity)
        if not event_ok:
            transaction.set_rollback(True)
            return False
        if order.seat_keys:
            seats_ok = EventSeat.objects.filter(
                event_id=order.event_id, seat_key__in=order.seat_keys,
                status=SeatStatus.AVAILABLE,
            ).update(status=SeatStatus.SOLD, order=order)
            if seats_ok != len(order.seat_keys):
                transaction.set_rollback(True)
                return False
    return True


def mark_order_failed(order: Order) -> bool:
    """PAYMENT_PENDING -> FAILED and release the reserved capacity."""
    with transaction.atomic():
        claimed = Order.objects.filter(
            pk=order.pk, status__in=[OrderStatus.CREATED, OrderStatus.PAYMENT_PENDING]
        ).update(status=OrderStatus.FAILED)
        if not claimed:
            return False
        release_capacity(order)
    order.refresh_from_db()
    log_action("order.payment_failed", order)
    return True


def mark_order_refunded(order: Order) -> bool:
    """PAID -> REFUNDED (the whole rest of the order); seats go back on sale."""
    order.refresh_from_db()
    remaining = order.amount_gross - order.refunded_amount
    with transaction.atomic():
        claimed = Order.objects.filter(pk=order.pk, status=OrderStatus.PAID).update(
            status=OrderStatus.REFUNDED, refunded_at=timezone.now()
        )
        if not claimed:
            return False
        release_capacity(order)
        Order.objects.filter(pk=order.pk).update(
            refunded_amount=order.amount_gross, refunded_quantity=order.quantity
        )
        # partial refunds still waiting are covered by this full one
        OrderRefund.objects.filter(order=order, status=RefundStatus.REQUESTED).update(
            status=RefundStatus.FAILED, completed_at=timezone.now()
        )
    order.refresh_from_db()
    log_action("order.refunded", order, metadata={"amount": str(remaining)})
    _invalidate_tickets_after_refund(order, remaining)
    return True


def apply_partial_refund(order: Order, refund_id) -> bool:
    """
    Provider confirmed a partial refund: its tickets become REFUNDED, their
    seats and capacity go back on sale, the order stays PAID (or becomes
    REFUNDED when nothing is left). Idempotent — a refund is applied once.
    """
    from apps.tickets.models import Ticket, TicketStatus

    now = timezone.now()
    with transaction.atomic():
        claimed = OrderRefund.objects.filter(
            pk=refund_id, order=order, status=RefundStatus.REQUESTED
        ).update(status=RefundStatus.SUCCEEDED, completed_at=now)
        if not claimed:
            return False
        refund = OrderRefund.objects.get(pk=refund_id)
        seat_ids = list(
            Ticket.objects.filter(order=order, id__in=refund.ticket_ids)
            .exclude(seat=None).values_list("seat_id", flat=True)
        )
        Ticket.objects.filter(
            order=order, id__in=refund.ticket_ids, status=TicketStatus.ISSUED
        ).update(status=TicketStatus.REFUNDED, refunded_at=now)
        EventSeat.objects.filter(
            pk__in=seat_ids, order=order, status=SeatStatus.SOLD
        ).update(status=SeatStatus.AVAILABLE, order=None)
        TicketPool.objects.filter(pk=order.pool_id).update(
            sold_count=models.F("sold_count") - refund.quantity
        )
        Event.objects.filter(pk=order.event_id).update(
            sold_total=models.F("sold_total") - refund.quantity
        )
        Order.objects.filter(pk=order.pk).update(
            refunded_amount=models.F("refunded_amount") + refund.amount,
            refunded_quantity=models.F("refunded_quantity") + refund.quantity,
        )
        order.refresh_from_db()
        if order.seat_keys:
            kept = list(EventSeat.objects.filter(order=order).order_by("sort_index"))
            price_of = dict(zip(order.seat_keys, order.seat_prices or [], strict=False))
            Order.objects.filter(pk=order.pk).update(
                seat_keys=[s.seat_key for s in kept], seat_labels=[s.label for s in kept],
                seat_prices=[price_of.get(s.seat_key, "") for s in kept],
            )
        if order.refunded_quantity >= order.quantity:
            Order.objects.filter(pk=order.pk, status=OrderStatus.PAID).update(
                status=OrderStatus.REFUNDED, refunded_at=now
            )
    order.refresh_from_db()
    log_action("order.partially_refunded", order, metadata={
        "amount": str(refund.amount), "tickets": refund.ticket_codes, "seats": refund.seat_labels,
    })
    try:
        from apps.tickets.services import queue_partial_refund_email
    except ImportError:
        return True
    queue_partial_refund_email(order, refund)
    return True


def _issue_tickets_if_configured(order: Order) -> None:
    """Hook for ticket issuing (implemented in apps.tickets, Phase 6)."""
    try:
        from apps.tickets.services import issue_tickets_for_order
    except ImportError:
        return
    issue_tickets_for_order(order)


def _invalidate_tickets_after_refund(order: Order, amount=None) -> None:
    try:
        from apps.tickets.services import invalidate_tickets_for_refunded_order
    except ImportError:
        return
    invalidate_tickets_for_refunded_order(order, amount)
