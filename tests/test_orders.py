import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.utils import timezone

from apps.events.models import EventStatus
from apps.orders import services
from apps.orders.models import Order, OrderStatus, PaymentConfig, PaymentMode
from tests.factories import make_event, make_order, make_pool

pytestmark = pytest.mark.django_db

NOW = timezone.now()


class TestCreateOrder:
    def test_happy_path_reserves_capacity(self):
        event = make_event(capacity_total=100)
        pool = make_pool(event, capacity=50, price_gross=Decimal("60.00"))
        order = services.create_order(event, "kupiec@example.com", 3)

        event.refresh_from_db()
        pool.refresh_from_db()
        assert event.sold_total == 3
        assert pool.sold_count == 3
        assert order.status == OrderStatus.CREATED
        assert order.amount_gross == Decimal("180.00")
        assert order.expires_at is not None
        assert order.is_demo is True  # default PaymentConfig mode is DEMO
        assert order.currency == "DEMO"
        assert order.payment_provider == "DEMO"

    def test_live_mode_sets_real_currency_and_provider(self):
        config = PaymentConfig.load()
        config.mode = PaymentMode.LIVE
        config.save()
        event = make_event()
        make_pool(event)
        order = services.create_order(event, "k@example.com", 1)
        assert order.is_demo is False
        assert order.currency == "PLN"
        assert order.payment_provider == "STRIPE"

    def test_rejects_quantity_above_event_limit(self):
        event = make_event(max_tickets_per_order=4)
        make_pool(event)
        with pytest.raises(services.PurchaseError):
            services.create_order(event, "k@example.com", 5)

    def test_rejects_zero_quantity(self):
        event = make_event()
        make_pool(event)
        with pytest.raises(services.PurchaseError):
            services.create_order(event, "k@example.com", 0)

    def test_rejects_unpublished_event(self):
        event = make_event(status=EventStatus.DRAFT)
        make_pool(event)
        with pytest.raises(services.PurchaseError):
            services.create_order(event, "k@example.com", 1)

    def test_rejects_when_sales_closed(self):
        event = make_event(sales_end_at=NOW - timedelta(hours=1))
        make_pool(event)
        with pytest.raises(services.PurchaseError):
            services.create_order(event, "k@example.com", 1)

    def test_rejects_when_pool_capacity_short(self):
        event = make_event(capacity_total=100)
        pool = make_pool(event, capacity=2)
        pool.sold_count = 1
        pool.save()
        with pytest.raises(services.CapacityError):
            services.create_order(event, "k@example.com", 2)

    def test_event_capacity_caps_pool_capacity(self):
        # Pool capacities may exceed event capacity; event total is the hard cap.
        event = make_event(capacity_total=3)
        make_pool(event, capacity=10)
        services.create_order(event, "a@example.com", 2)
        with pytest.raises(services.CapacityError):
            services.create_order(event, "b@example.com", 2)
        event.refresh_from_db()
        assert event.sold_total == 2

    def test_failed_event_reservation_rolls_back_pool_reservation(self):
        event = make_event(capacity_total=1)
        pool = make_pool(event, capacity=10)
        with pytest.raises(services.CapacityError):
            services.create_order(event, "k@example.com", 2)
        pool.refresh_from_db()
        assert pool.sold_count == 0


class TestOrderExpiry:
    def make_expired_order(self):
        event = make_event()
        pool = make_pool(event)
        order = services.create_order(event, "k@example.com", 2)
        Order.objects.filter(pk=order.pk).update(expires_at=NOW - timedelta(minutes=1))
        order.refresh_from_db()
        return event, pool, order

    def test_expiry_releases_capacity(self):
        event, pool, order = self.make_expired_order()
        count = services.expire_stale_orders()
        assert count == 1
        event.refresh_from_db()
        pool.refresh_from_db()
        order.refresh_from_db()
        assert order.status == OrderStatus.EXPIRED
        assert event.sold_total == 0
        assert pool.sold_count == 0

    def test_double_expiry_releases_capacity_once(self):
        event, pool, order = self.make_expired_order()
        assert services.expire_order(order) is True
        assert services.expire_order(order) is False
        event.refresh_from_db()
        pool.refresh_from_db()
        assert event.sold_total == 0
        assert pool.sold_count == 0

    def test_paid_order_is_not_expired(self):
        event = make_event()
        pool = make_pool(event)
        order = make_order(event, pool, status=OrderStatus.PAID,
                           expires_at=NOW - timedelta(minutes=5))
        assert services.expire_stale_orders() == 0
        order.refresh_from_db()
        assert order.status == OrderStatus.PAID

    def test_cancel_unpaid_order_releases_capacity_once(self):
        event = make_event()
        make_pool(event)
        order = services.create_order(event, "k@example.com", 1)
        assert services.cancel_unpaid_order(order) is True
        assert services.cancel_unpaid_order(order) is False
        event.refresh_from_db()
        assert event.sold_total == 0


@pytest.mark.django_db(transaction=True)
class TestConcurrentPurchases:
    def test_concurrent_buyers_never_oversell(self):
        """8 buyers race for the last 5 tickets — exactly 5 must win."""
        event = make_event(capacity_total=5)
        pool = make_pool(event, capacity=5)
        results = []
        barrier = threading.Barrier(8)

        def buy():
            try:
                barrier.wait(timeout=10)
                services.create_order(event, "race@example.com", 1)
                results.append("ok")
            except services.PurchaseError:
                results.append("rejected")
            except Exception as exc:  # noqa: BLE001 - diagnostic in test
                results.append(f"error:{exc}")
            finally:
                connection.close()

        threads = [threading.Thread(target=buy) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(results) == 8
        assert results.count("ok") == 5, results
        assert results.count("rejected") == 3, results
        event.refresh_from_db()
        pool.refresh_from_db()
        assert event.sold_total == 5
        assert pool.sold_count == 5


class TestPurchaseView:
    def test_post_creates_order_and_redirects(self, client):
        event = make_event()
        make_pool(event)
        response = client.post(
            f"/kup/{event.slug}/", {"buyer_email": "k@example.com", "quantity": 2}
        )
        assert response.status_code == 302
        order = Order.objects.get()
        # Purchase leads straight to the provider checkout (demo mode -> demo cash desk).
        assert response.url == f"/kasa-demo/{order.id}/"
        assert order.quantity == 2

    def test_invalid_email_redirects_back_with_error(self, client):
        event = make_event()
        make_pool(event)
        response = client.post(
            f"/kup/{event.slug}/", {"buyer_email": "niepoprawny", "quantity": 1}, follow=True
        )
        assert "Sprawdź poprawność" in response.content.decode()
        assert Order.objects.count() == 0

    def test_get_not_allowed(self, client):
        event = make_event()
        make_pool(event)
        assert client.get(f"/kup/{event.slug}/").status_code == 405

    def test_order_detail_page(self, client):
        event = make_event()
        make_pool(event)
        order = services.create_order(event, "k@example.com", 1)
        content = client.get(f"/zamowienie/{order.id}/").content.decode()
        assert order.short_id in content
        assert "oczekuje na płatność" in content.lower()

    def test_rate_limit_blocks_excessive_purchases(self, client, settings):
        settings.CACHES = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                        "LOCATION": "ratelimit-test"}
        }
        event = make_event(capacity_total=500)
        make_pool(event, capacity=500)
        for _ in range(10):
            client.post(f"/kup/{event.slug}/",
                        {"buyer_email": "k@example.com", "quantity": 1})
        response = client.post(
            f"/kup/{event.slug}/", {"buyer_email": "k@example.com", "quantity": 1},
            follow=True,
        )
        assert "Zbyt wiele prób" in response.content.decode()
        assert Order.objects.count() == 10
