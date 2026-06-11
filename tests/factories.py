"""Plain factory helpers shared by the test suite."""

import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.events.models import Event, EventStatus, TicketPool
from apps.orders.models import Order, OrderStatus, PaymentProviderKind
from apps.tickets.models import Ticket


def make_event(**kwargs) -> Event:
    now = timezone.now()
    defaults = {
        "title": "Testowy koncert",
        "slug": f"testowy-koncert-{uuid.uuid4().hex[:6]}",
        "start_at": now + timedelta(days=7),
        "end_at": now + timedelta(days=7, hours=4),
        "sales_start_at": now - timedelta(days=1),
        "sales_end_at": now + timedelta(days=7),
        "capacity_total": 300,
        "status": EventStatus.PUBLISHED,
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def make_pool(event: Event, **kwargs) -> TicketPool:
    defaults = {
        "name": "Regular",
        "priority": kwargs.pop("priority", 1),
        "price_gross": Decimal("60.00"),
        "capacity": 100,
    }
    defaults.update(kwargs)
    return TicketPool.objects.create(event=event, **defaults)


def make_order(event: Event, pool: TicketPool, **kwargs) -> Order:
    defaults = {
        "buyer_email": "kupujacy@example.com",
        "quantity": 1,
        "amount_gross": pool.price_gross,
        "currency": pool.currency,
        "status": OrderStatus.CREATED,
        "payment_provider": PaymentProviderKind.DEMO,
        "is_demo": True,
    }
    defaults.update(kwargs)
    return Order.objects.create(event=event, pool=pool, **defaults)


def make_ticket(order: Order, raw_token: str | None = None, **kwargs) -> Ticket:
    raw_token = raw_token or uuid.uuid4().hex
    defaults = {
        "buyer_email": order.buyer_email,
        # Same shape as production codes: XXXX-NN with a dash.
        "short_code": f"T{uuid.uuid4().hex[:3].upper()}-{uuid.uuid4().int % 90 + 10}",
        "qr_token_hash": hashlib.sha256(raw_token.encode()).hexdigest(),
        "is_demo": order.is_demo,
    }
    defaults.update(kwargs)
    return Ticket.objects.create(event=order.event, pool=order.pool, order=order, **defaults)
