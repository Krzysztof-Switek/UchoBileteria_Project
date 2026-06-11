"""
DemoProvider: a full PaymentProvider implementation backed by virtual money.

Demo "webhooks" are signed with HMAC and flow through exactly the same
pipeline as real provider webhooks (signature check -> payment_events ->
idempotency -> order transition). Only the money source is simulated.
"""

import hashlib
import hmac
import json
import uuid

from django.conf import settings
from django.db import transaction
from django.urls import reverse

from apps.orders.models import Order, OrderStatus, PaymentProviderKind


def _secret() -> bytes:
    return f"demo-webhook:{settings.SECRET_KEY}".encode()


def sign(body: bytes) -> str:
    return hmac.new(_secret(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(body), signature)


def build_event_body(event_type: str, order: Order, event_id: str | None = None) -> bytes:
    payload = {
        "id": event_id or f"demo_evt_{uuid.uuid4().hex}",
        "type": event_type,
        "session_id": order.payment_session_id,
        "order_id": str(order.id),
        "amount": str(order.amount_gross),
        "currency": order.currency,
    }
    return json.dumps(payload).encode()


def deliver(body: bytes, signature: str):
    """
    Single entry point for demo webhooks — used by the HTTP endpoint and by
    the demo checkout buttons, so both exercise the same production pipeline.
    """
    from apps.orders.payments import SignatureError, process_webhook

    if not verify_signature(body, signature):
        raise SignatureError("Nieprawidłowy podpis webhooka demo.")
    payload = json.loads(body)
    return process_webhook(
        PaymentProviderKind.DEMO,
        payload,
        payload_hash=hashlib.sha256(body).hexdigest(),
    )


class DemoProvider:
    def start_checkout(self, order: Order) -> str:
        with transaction.atomic():
            claimed = Order.objects.filter(
                pk=order.pk, status=OrderStatus.CREATED
            ).update(
                status=OrderStatus.PAYMENT_PENDING,
                payment_session_id=f"demo_sess_{uuid.uuid4().hex}",
            )
        if not claimed and order.status not in (
            OrderStatus.PAYMENT_PENDING,  # already at checkout — just go back there
        ):
            raise ValueError("Zamówienie nie oczekuje na płatność.")
        order.refresh_from_db()
        return reverse("orders:demo_checkout", kwargs={"order_id": order.id})

    def refund(self, order: Order, amount=None) -> str:
        """Demo refund: emit a refund webhook through the standard pipeline."""
        body = build_event_body("refund.succeeded", order)
        deliver(body, sign(body))
        return json.loads(body)["id"]
