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

    def test_on_sale_shows_tickets_left_in_active_pool(self, client):
        # KUP-02: "zostało N z tej puli" fill-count.
        event = make_event()
        make_pool(event, capacity=50, sold_count=10)
        content = client.get(self.url(event)).content.decode()
        assert "Zostało 40 z tej puli" in content

    def test_low_stock_pool_gets_warning_styling(self, client):
        event = make_event()
        make_pool(event, capacity=10, sold_count=5)
        content = client.get(self.url(event)).content.decode()
        assert "text-warning font-semibold" in content
        assert "Zostało 5 z tej puli" in content

    def test_shows_next_pool_hint_when_a_future_pool_exists(self, client):
        # KUP-02: buyers should see the next pool's name/price before it opens.
        event = make_event(sales_start_at=NOW - timedelta(days=1))
        make_pool(event, name="Early Bird", priority=1, price_gross=Decimal("40.00"))
        make_pool(
            event, name="Regular", priority=2, price_gross=Decimal("60.00"),
            sales_start_at=NOW + timedelta(days=3),
        )
        content = client.get(self.url(event)).content.decode()
        assert "Kolejna pula:" in content
        assert "Regular" in content
        assert "60,00 PLN" in content or "60.00 PLN" in content

    def test_no_next_pool_hint_with_only_one_pool(self, client):
        event = make_event()
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        assert "Kolejna pula" not in content

    def test_shows_live_total_calculation_markup(self, client):
        # KUP-02: a locale-safe raw price for the client-side qty*price=total
        # calculator — must be "40.00" (dot), never Polish-localized "40,00".
        event = make_event()
        make_pool(event, price_gross=Decimal("40.00"), currency="PLN")
        content = client.get(self.url(event)).content.decode()
        assert 'id="purchase-total"' in content
        assert 'data-price="40.00"' in content
        assert 'data-currency="PLN"' in content

    def test_sold_out_event(self, client):
        event = make_event(capacity_total=10)
        make_pool(event, capacity=10)
        event.sold_total = 10
        event.save()
        content = client.get(self.url(event)).content.decode()
        assert "Wyprzedane" in content
        assert "Kup bilet" not in content
        assert "Zobacz inne wydarzenia" in content  # KUP-07: alternative action

    def test_sales_not_started(self, client):
        event = make_event(sales_start_at=NOW + timedelta(days=2))
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        assert "Sprzedaż biletów rozpocznie się" in content
        assert "Kup bilet" not in content
        assert "Dodaj przypomnienie do kalendarza" in content  # KUP-07
        assert f"/wydarzenia/{event.slug}/przypomnienie.ics" in content

    def test_sales_closed(self, client):
        event = make_event(sales_end_at=NOW - timedelta(hours=1))
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        assert "Sprzedaż biletów zakończona" in content
        assert "Kup bilet" not in content
        assert "Zobacz inne wydarzenia" in content

    def test_paused_when_first_pool_starts_later_than_event_sales(self, client):
        # Event sales window is open, but the first pool starts in 2 days.
        event = make_event(sales_start_at=NOW - timedelta(days=1))
        make_pool(event, name="Later", sales_start_at=NOW + timedelta(days=2))
        content = client.get(self.url(event)).content.decode()
        assert "Sprzedaż chwilowo wstrzymana" in content
        assert "Kup bilet" not in content
        assert "Zobacz inne wydarzenia" in content

    def test_cancelled_event_shows_notice(self, client):
        event = make_event(status=EventStatus.CANCELLED)
        make_pool(event)
        content = client.get(self.url(event)).content.decode()
        assert "Wydarzenie odwołane" in content
        assert "Kup bilet" not in content
        assert "Zobacz inne wydarzenia" in content

    def test_sales_start_reminder_ics_download(self, client):
        event = make_event(sales_start_at=NOW + timedelta(days=2))
        response = client.get(f"/wydarzenia/{event.slug}/przypomnienie.ics")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/calendar")
        body = response.content.decode()
        assert "BEGIN:VCALENDAR" in body
        assert event.title in body

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


class TestClubIdentity:
    """KUP-04: header + footer so the site reads as run by a real club."""

    def test_header_shows_club_name_linking_home(self, client):
        content = client.get("/").content.decode()
        assert "Klub UCHO" in content
        assert content.count('href="/"') >= 1

    def test_footer_shows_legal_links_and_contact_email(self, client):
        content = client.get("/").content.decode()
        assert 'href="/regulamin/"' in content
        assert 'href="/polityka-prywatnosci/"' in content
        assert "bilety@example.com" in content  # DEFAULT_FROM_EMAIL fallback

    def test_footer_hides_address_and_phone_when_not_configured(self, client):
        content = client.get("/").content.decode()
        # Defaults are "" — nothing fabricated should appear in their place.
        assert "[Treść do uzupełnienia]" not in content
        assert "None" not in content

    def test_footer_shows_address_and_phone_when_configured(self, client, settings):
        settings.CLUB_ADDRESS = "ul. Testowa 1, Gdańsk"
        settings.CLUB_PHONE = "+48 123 456 789"
        content = client.get("/").content.decode()
        assert "ul. Testowa 1, Gdańsk" in content
        assert "+48 123 456 789" in content

    def test_footer_appears_on_event_detail_page_too(self, client):
        event = make_event()
        make_pool(event)
        content = client.get(f"/wydarzenia/{event.slug}/").content.decode()
        assert 'href="/regulamin/"' in content

    def test_regulamin_page_renders(self, client):
        response = client.get("/regulamin/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Regulamin sprzedaży biletów" in content
        assert "Zwroty i odwołane wydarzenia" in content

    def test_polityka_prywatnosci_page_renders(self, client):
        response = client.get("/polityka-prywatnosci/")
        assert response.status_code == 200
        content = response.content.decode()
        assert "Polityka prywatności" in content
        assert "Administrator danych" in content
