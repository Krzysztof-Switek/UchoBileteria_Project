"""
StripeProvider: real-money payments behind the same PaymentProvider
interface as the demo. Stripe events are normalized into the internal
webhook payload shape and flow through the shared idempotent pipeline.
"""

from decimal import Decimal

from django.conf import settings
from django.db import transaction

from apps.orders.models import Order, OrderStatus


def _stripe():
    import stripe

    stripe.api_key = settings.STRIPE_SECRET_KEY
    return stripe


def stripe_configured() -> bool:
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRET)


class StripeProvider:
    def start_checkout(self, order: Order) -> str:
        # Re-entry: an existing session keeps its hosted checkout URL.
        if order.status == OrderStatus.PAYMENT_PENDING and order.checkout_url:
            return order.checkout_url
        if order.status != OrderStatus.CREATED:
            raise ValueError("Zamówienie nie oczekuje na płatność.")

        stripe = _stripe()
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": order.currency.lower(),
                        "unit_amount": int(order.pool.price_gross * 100),
                        "product_data": {"name": f"Bilet: {order.event.title}"},
                    },
                    "quantity": order.quantity,
                }
            ],
            customer_email=order.buyer_email,
            metadata={"order_id": str(order.id)},
            success_url=f"{settings.SITE_BASE_URL}/zamowienie/{order.id}/",
            cancel_url=f"{settings.SITE_BASE_URL}/zamowienie/{order.id}/",
            expires_at=int(order.expires_at.timestamp()) if order.expires_at else None,
        )
        with transaction.atomic():
            Order.objects.filter(pk=order.pk, status=OrderStatus.CREATED).update(
                status=OrderStatus.PAYMENT_PENDING,
                payment_session_id=session["id"],
                checkout_url=session["url"],
            )
        order.refresh_from_db()
        return order.checkout_url

    def refund(self, order: Order, amount=None) -> str:
        if not order.provider_order_id:
            raise ValueError("Brak identyfikatora płatności Stripe dla tego zamówienia.")
        stripe = _stripe()
        refund = stripe.Refund.create(payment_intent=order.provider_order_id)
        # The REFUNDED status lands asynchronously via the charge.refunded webhook.
        return refund["id"]


def handle_stripe_event(event: dict):
    """Normalize a verified Stripe event and push it through the pipeline."""
    from apps.orders.models import PaymentProviderKind
    from apps.orders.payments import process_webhook

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        order_id = (obj.get("metadata") or {}).get("order_id")
        if order_id and obj.get("payment_intent"):
            # Remember the payment intent — refunds need it.
            Order.objects.filter(id=order_id).update(
                provider_order_id=obj["payment_intent"]
            )
        payload = {
            "id": event["id"],
            "type": "payment.succeeded",
            "order_id": order_id,
            "amount": Decimal(obj["amount_total"]) / 100 if obj.get("amount_total") else None,
        }
    elif event_type == "checkout.session.expired":
        payload = {
            "id": event["id"],
            "type": "payment.failed",
            "order_id": (obj.get("metadata") or {}).get("order_id"),
            "amount": None,
        }
    elif event_type == "charge.refunded":
        order = Order.objects.filter(
            provider_order_id=obj.get("payment_intent") or "__none__"
        ).first()
        payload = {
            "id": event["id"],
            "type": "refund.succeeded",
            "order_id": str(order.id) if order else None,
            "amount": (
                Decimal(obj["amount_refunded"]) / 100 if obj.get("amount_refunded") else None
            ),
        }
    else:
        # Recorded for audit, processed as IGNORED.
        payload = {"id": event["id"], "type": event_type, "order_id": None, "amount": None}

    return process_webhook(PaymentProviderKind.STRIPE, payload)
