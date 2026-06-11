"""StripeProvider tests with a mocked Stripe SDK (no network, no real keys)."""

from decimal import Decimal

import pytest

from apps.orders import refunds
from apps.orders.models import (
    Order,
    OrderStatus,
    PaymentConfig,
    PaymentEvent,
    PaymentEventStatus,
    PaymentMode,
)
from apps.orders.providers.stripe_provider import StripeProvider, handle_stripe_event
from apps.tickets.models import TicketStatus
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


@pytest.fixture
def live_mode(settings):
    settings.STRIPE_SECRET_KEY = "sk_test_x"
    settings.STRIPE_WEBHOOK_SECRET = "whsec_x"
    config = PaymentConfig.load()
    config.mode = PaymentMode.LIVE
    config.save()


@pytest.fixture
def fake_stripe(monkeypatch):
    """Replace the stripe SDK calls used by the provider."""
    import stripe

    calls = {"sessions": [], "refunds": []}

    def fake_session_create(**kwargs):
        calls["sessions"].append(kwargs)
        return {"id": "cs_test_123", "url": "https://checkout.stripe.com/c/cs_test_123"}

    def fake_refund_create(**kwargs):
        calls["refunds"].append(kwargs)
        return {"id": "re_test_123"}

    monkeypatch.setattr(stripe.checkout.Session, "create", fake_session_create)
    monkeypatch.setattr(stripe.Refund, "create", fake_refund_create)
    return calls


def live_order(client, quantity=2):
    event = make_event()
    make_pool(event, price_gross=Decimal("60.00"))
    client.post(f"/kup/{event.slug}/", {"buyer_email": "k@example.com", "quantity": quantity})
    return event, Order.objects.latest("created_at")


def completed_event_payload(order, event_id="evt_1"):
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"order_id": str(order.id)},
                "payment_intent": "pi_test_123",
                "amount_total": int(order.amount_gross * 100),
            }
        },
    }


class TestStripeCheckout:
    def test_purchase_in_live_mode_uses_stripe(self, client, live_mode, fake_stripe):
        event, order = live_order(client)
        assert order.is_demo is False
        assert order.payment_provider == "STRIPE"
        assert order.currency == "PLN"
        assert order.status == OrderStatus.PAYMENT_PENDING
        assert order.payment_session_id == "cs_test_123"
        assert order.checkout_url.startswith("https://checkout.stripe.com/")

        [session] = fake_stripe["sessions"]
        assert session["metadata"]["order_id"] == str(order.id)
        assert session["line_items"][0]["price_data"]["unit_amount"] == 6000
        assert session["line_items"][0]["quantity"] == 2

    def test_reentry_reuses_existing_session(self, client, live_mode, fake_stripe):
        event, order = live_order(client)
        url = StripeProvider().start_checkout(order)
        assert url == order.checkout_url
        assert len(fake_stripe["sessions"]) == 1  # no second session created


class TestStripeWebhook:
    def test_completed_session_pays_order_and_issues_tickets(
        self, client, live_mode, fake_stripe
    ):
        event, order = live_order(client)
        record = handle_stripe_event(completed_event_payload(order))
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID
        assert order.provider_order_id == "pi_test_123"
        assert record.processing_status == PaymentEventStatus.PROCESSED
        assert record.is_demo is False
        assert order.tickets.count() == 2

    def test_duplicate_stripe_event_is_noop(self, client, live_mode, fake_stripe):
        event, order = live_order(client)
        handle_stripe_event(completed_event_payload(order, "evt_dup"))
        handle_stripe_event(completed_event_payload(order, "evt_dup"))
        assert order.tickets.count() == 2
        assert PaymentEvent.objects.filter(provider_event_id="evt_dup").count() == 1

    def test_expired_session_fails_order_and_releases_capacity(
        self, client, live_mode, fake_stripe
    ):
        event, order = live_order(client)
        handle_stripe_event({
            "id": "evt_exp",
            "type": "checkout.session.expired",
            "data": {"object": {"metadata": {"order_id": str(order.id)}}},
        })
        order.refresh_from_db()
        event.refresh_from_db()
        assert order.status == OrderStatus.FAILED
        assert event.sold_total == 0

    def test_unknown_event_type_recorded_as_ignored(self, live_mode):
        record = handle_stripe_event({
            "id": "evt_x", "type": "payout.paid", "data": {"object": {}},
        })
        assert record.processing_status == PaymentEventStatus.IGNORED

    def test_webhook_endpoint_rejects_bad_signature(self, client, live_mode, settings):
        response = client.post(
            "/webhooks/stripe/",
            data=b"{}",
            content_type="application/json",
            headers={"Stripe-Signature": "zly"},
        )
        assert response.status_code == 400

    def test_webhook_endpoint_with_verified_event(
        self, client, live_mode, fake_stripe, monkeypatch
    ):
        import stripe

        event, order = live_order(client)
        payload = completed_event_payload(order, "evt_http")
        monkeypatch.setattr(
            stripe.Webhook, "construct_event", lambda *args, **kwargs: payload
        )
        response = client.post(
            "/webhooks/stripe/",
            data=b"{}",
            content_type="application/json",
            headers={"Stripe-Signature": "ok"},
        )
        assert response.status_code == 200
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID


class TestStripeRefund:
    def paid_live_order(self, client, fake_stripe):
        event, order = live_order(client)
        handle_stripe_event(completed_event_payload(order))
        order.refresh_from_db()
        return event, order

    def test_refund_calls_stripe_and_webhook_completes_it(self, client, live_mode,
                                                          fake_stripe):
        event, order = self.paid_live_order(client, fake_stripe)
        ref = refunds.refund_order(order)
        assert ref == "re_test_123"
        assert fake_stripe["refunds"] == [{"payment_intent": "pi_test_123"}]

        # Stripe confirms asynchronously:
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID  # not yet
        handle_stripe_event({
            "id": "evt_ref",
            "type": "charge.refunded",
            "data": {"object": {"payment_intent": "pi_test_123",
                                 "amount_refunded": int(order.amount_gross * 100)}},
        })
        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED
        for ticket in order.tickets.all():
            assert ticket.status == TicketStatus.REFUNDED

    def test_demo_order_refund_never_calls_stripe(self, client, fake_stripe):
        # default mode is DEMO
        event = make_event()
        make_pool(event)
        client.post(f"/kup/{event.slug}/", {"buyer_email": "k@example.com", "quantity": 1})
        order = Order.objects.latest("created_at")
        client.post(f"/kasa-demo/{order.id}/zaplac/")
        order.refresh_from_db()
        refunds.refund_order(order)
        assert fake_stripe["refunds"] == []
        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED
