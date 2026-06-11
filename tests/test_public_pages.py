from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.events.models import EventStatus
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db

NOW = timezone.now()


class TestEventList:
    def test_shows_only_published_upcoming_events(self, client):
        published = make_event(title="Koncert A")
        make_event(title="Szkic", status=EventStatus.DRAFT)
        make_event(title="Odwołany", status=EventStatus.CANCELLED)
        make_event(
            title="Miniony",
            start_at=NOW - timedelta(days=3),
            end_at=NOW - timedelta(days=3, hours=-4),
            sales_start_at=NOW - timedelta(days=30),
            sales_end_at=NOW - timedelta(days=4),
        )
        response = client.get("/")
        content = response.content.decode()
        assert response.status_code == 200
        assert published.title in content
        assert "Szkic" not in content
        assert "Odwołany" not in content
        assert "Miniony" not in content

    def test_empty_list_message(self, client):
        response = client.get("/")
        assert "Brak nadchodzących wydarzeń" in response.content.decode()


class TestEventDetail:
    def url(self, event):
        return f"/wydarzenia/{event.slug}/"

    def test_draft_event_is_404(self, client):
        event = make_event(status=EventStatus.DRAFT)
        assert client.get(self.url(event)).status_code == 404

    def test_on_sale_shows_active_pool_price_and_buy_button(self, client):
        event = make_event()
        make_pool(event, name="Early Bird", price_gross=Decimal("40.00"))
        content = client.get(self.url(event)).content.decode()
        assert "40,00 PLN" in content or "40.00 PLN" in content
        assert "Early Bird" in content
        assert "Kup bilet" in content

    def test_sold_out_event(self, client):
        event = make_event(capacity_total=10)
        make_pool(event, capacity=10)
        event.sold_total = 10
        event.save()
        content = client.get(self.url(event)).content.decode()
        assert "Wyprzedane" in content
        assert "Kup bilet" not in content

    def test_sales_not_started(self, client):
        event = make_event(sales_start_at=NOW + timedelta(days=2))
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        assert "Sprzedaż biletów rozpocznie się" in content
        assert "Kup bilet" not in content

    def test_sales_closed(self, client):
        event = make_event(sales_end_at=NOW - timedelta(hours=1))
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        assert "Sprzedaż biletów zakończona" in content
        assert "Kup bilet" not in content

    def test_paused_when_first_pool_starts_later_than_event_sales(self, client):
        # Event sales window is open, but the first pool starts in 2 days.
        event = make_event(sales_start_at=NOW - timedelta(days=1))
        make_pool(event, name="Later", sales_start_at=NOW + timedelta(days=2))
        content = client.get(self.url(event)).content.decode()
        assert "Sprzedaż chwilowo wstrzymana" in content
        assert "Kup bilet" not in content

    def test_cancelled_event_shows_notice(self, client):
        event = make_event(status=EventStatus.CANCELLED)
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        assert "Wydarzenie odwołane" in content
        assert "Kup bilet" not in content

    def test_demo_banner_shown_in_demo_mode(self, client):
        event = make_event()
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        assert "TRYB DEMO" in content

    def test_no_internal_id_urls_exposed(self, client):
        event = make_event()
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        # Public links use slugs, never numeric pks.
        assert f'/wydarzenia/{event.pk}/' not in content
        assert f'/wydarzenia/{event.slug}/' in content or 'wydarzenia' in content


class TestPoolPriceChanges:
    def test_price_switches_to_next_pool_after_sellout(self, client):
        event = make_event()
        early = make_pool(event, name="Early", priority=1, capacity=10,
                          price_gross=Decimal("40.00"))
        make_pool(event, name="Regular", priority=2, capacity=50,
                  price_gross=Decimal("60.00"))
        url = f"/wydarzenia/{event.slug}/"

        content = client.get(url).content.decode()
        assert "40,00 PLN" in content or "40.00 PLN" in content

        early.sold_count = 10
        early.save()
        content = client.get(url).content.decode()
        assert "60,00 PLN" in content or "60.00 PLN" in content
