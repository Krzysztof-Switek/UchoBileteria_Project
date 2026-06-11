import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.orders import services
from apps.orders.models import (
    Order,
    OrderStatus,
    PaymentEvent,
    PaymentEventStatus,
    PaymentProviderKind,
)
from apps.orders.payments import process_webhook
from apps.orders.providers import demo
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


def start_paid_checkout(client, event=None, quantity=1):
    """Create an order through the public flow and land on the demo checkout."""
    if event is None:
        event = make_event()
        make_pool(event)
    response = client.post(
        f"/kup/{event.slug}/", {"buyer_email": "k@example.com", "quantity": quantity}
    )
    order = Order.objects.latest("created_at")
    assert response.url == f"/kasa-demo/{order.id}/"
    return event, order


class TestDemoHappyPath:
    def test_purchase_redirects_to_demo_checkout_and_sets_pending(self, client):
        event, order = start_paid_checkout(client)
        assert order.status == OrderStatus.PAYMENT_PENDING
        assert order.payment_session_id.startswith("demo_sess_")
        content = client.get(f"/kasa-demo/{order.id}/").content.decode()
        assert "kasa demo" in content.lower()
        assert "DEMO" in content

    def test_demo_pay_marks_order_paid_via_webhook_pipeline(self, client):
        event, order = start_paid_checkout(client)
        response = client.post(f"/kasa-demo/{order.id}/zaplac/")
        assert response.status_code == 302
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID
        assert order.paid_at is not None
        record = PaymentEvent.objects.get()
        assert record.provider == PaymentProviderKind.DEMO
        assert record.processing_status == PaymentEventStatus.PROCESSED
        assert record.event_type == "payment.succeeded"
        assert record.is_demo is True

    def test_demo_decline_fails_order_and_releases_capacity(self, client):
        event, order = start_paid_checkout(client, quantity=2)
        client.post(f"/kasa-demo/{order.id}/odrzuc/")
        order.refresh_from_db()
        event.refresh_from_db()
        assert order.status == OrderStatus.FAILED
        assert event.sold_total == 0

    def test_demo_abandon_cancels_and_releases(self, client):
        event, order = start_paid_checkout(client)
        client.post(f"/kasa-demo/{order.id}/porzuc/")
        order.refresh_from_db()
        event.refresh_from_db()
        assert order.status == OrderStatus.CANCELLED
        assert event.sold_total == 0

    def test_checkout_of_paid_order_redirects_to_detail(self, client):
        event, order = start_paid_checkout(client)
        client.post(f"/kasa-demo/{order.id}/zaplac/")
        response = client.get(f"/kasa-demo/{order.id}/")
        assert response.status_code == 302
        assert response.url == f"/zamowienie/{order.id}/"


class TestWebhookIdempotency:
    def test_duplicate_webhook_is_noop(self, client):
        event, order = start_paid_checkout(client)
        body = demo.build_event_body("payment.succeeded", order, event_id="demo_evt_X")
        demo.deliver(body, demo.sign(body))
        demo.deliver(body, demo.sign(body))  # duplicate delivery
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID
        assert PaymentEvent.objects.count() == 1

    def test_two_different_success_events_process_once(self, client):
        event, order = start_paid_checkout(client)
        body1 = demo.build_event_body("payment.succeeded", order, event_id="evt_1")
        body2 = demo.build_event_body("payment.succeeded", order, event_id="evt_2")
        demo.deliver(body1, demo.sign(body1))
        record2 = demo.deliver(body2, demo.sign(body2))
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID
        # second event recorded but had no effect
        assert record2.processing_status == PaymentEventStatus.IGNORED


class TestWebhookEndpoint:
    def test_http_webhook_with_valid_signature(self, client):
        event, order = start_paid_checkout(client)
        body = demo.build_event_body("payment.succeeded", order)
        response = client.post(
            "/webhooks/demo/",
            data=body,
            content_type="application/json",
            headers={"X-Demo-Signature": demo.sign(body)},
        )
        assert response.status_code == 200
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID

    def test_http_webhook_with_bad_signature_rejected(self, client):
        event, order = start_paid_checkout(client)
        body = demo.build_event_body("payment.succeeded", order)
        response = client.post(
            "/webhooks/demo/",
            data=body,
            content_type="application/json",
            headers={"X-Demo-Signature": "0" * 64},
        )
        assert response.status_code == 400
        order.refresh_from_db()
        assert order.status == OrderStatus.PAYMENT_PENDING
        assert PaymentEvent.objects.count() == 0

    def test_webhook_for_unknown_order_records_error(self, client):
        body = json.dumps({
            "id": "demo_evt_ghost",
            "type": "payment.succeeded",
            "order_id": "00000000-0000-0000-0000-000000000000",
            "amount": "60.00",
        }).encode()
        response = client.post(
            "/webhooks/demo/",
            data=body,
            content_type="application/json",
            headers={"X-Demo-Signature": demo.sign(body)},
        )
        assert response.status_code == 200  # acknowledged, recorded as ERROR
        record = PaymentEvent.objects.get()
        assert record.processing_status == PaymentEventStatus.ERROR

    def test_unknown_event_type_is_ignored(self, client):
        event, order = start_paid_checkout(client)
        body = demo.build_event_body("payment.weird", order)
        client.post(
            "/webhooks/demo/",
            data=body,
            content_type="application/json",
            headers={"X-Demo-Signature": demo.sign(body)},
        )
        record = PaymentEvent.objects.get()
        assert record.processing_status == PaymentEventStatus.IGNORED
        order.refresh_from_db()
        assert order.status == OrderStatus.PAYMENT_PENDING


class TestLateWebhook:
    def expire(self, order):
        Order.objects.filter(pk=order.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        order.refresh_from_db()
        assert services.expire_order(order) is True
        order.refresh_from_db()

    def test_late_webhook_rereserves_capacity_and_pays(self, client):
        event, order = start_paid_checkout(client, quantity=2)
        self.expire(order)
        event.refresh_from_db()
        assert event.sold_total == 0

        body = demo.build_event_body("payment.succeeded", order)
        record = demo.deliver(body, demo.sign(body))
        order.refresh_from_db()
        event.refresh_from_db()
        assert order.status == OrderStatus.PAID
        assert event.sold_total == 2
        assert record.processing_status == PaymentEventStatus.PROCESSED

    def test_late_webhook_with_no_capacity_left_keeps_order_expired(self, client):
        event = make_event(capacity_total=2)
        make_pool(event, capacity=2)
        event, order = start_paid_checkout(client, event=event, quantity=2)
        self.expire(order)

        # Someone else takes the released seats.
        other = services.create_order(event, "inny@example.com", 2)
        assert other.status == OrderStatus.CREATED

        body = demo.build_event_body("payment.succeeded", order)
        record = demo.deliver(body, demo.sign(body))
        order.refresh_from_db()
        event.refresh_from_db()
        assert order.status == OrderStatus.EXPIRED
        assert event.sold_total == 2  # only the other order's seats
        assert record.processing_status == PaymentEventStatus.IGNORED

    def test_delayed_webhook_ui_flow(self, client):
        event, order = start_paid_checkout(client)
        response = client.post(f"/kasa-demo/{order.id}/zaplac-pozniej/")
        content = response.content.decode()
        assert "Dostarcz webhook teraz" in content
        order.refresh_from_db()
        assert order.status == OrderStatus.PAYMENT_PENDING  # not paid yet

        # Extract payload + signature from the rendered form and deliver.
        import re
        payload = re.search(r'name="payload" value="([^"]+)"', content).group(1)
        signature = re.search(r'name="signature" value="([^"]+)"', content).group(1)
        import html
        client.post(
            "/kasa-demo/dostarcz-webhook/",
            {"payload": html.unescape(payload), "signature": signature},
        )
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID


class TestProcessWebhookDirect:
    def test_amount_recorded_on_payment_event(self):
        event = make_event()
        make_pool(event)
        order = services.create_order(event, "k@example.com", 2)
        payload = json.loads(demo.build_event_body("payment.succeeded", order))
        record = process_webhook(PaymentProviderKind.DEMO, payload)
        assert str(record.amount) == str(order.amount_gross)
