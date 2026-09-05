import pytest

from apps.orders.models import OrderStatus
from tests.factories import make_event, make_order, make_pool, make_ticket

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client(client, django_user_model):
    user = django_user_model.objects.create_superuser(
        "search_staff", email="search_staff@klub.pl", password="x"
    )
    client.force_login(user)
    return client


class TestGlobalSearchNav:
    """OBS-04: the search box is reachable from any staff screen."""

    def test_search_box_present_on_admin_index(self, staff_client):
        body = staff_client.get("/admin/").content.decode()
        assert 'id="global-search-input"' in body
        assert '/szukaj/' in body

    def test_search_box_present_on_reports_dashboard(self, staff_client):
        body = staff_client.get("/raporty/").content.decode()
        assert 'id="global-search-input"' in body
        assert '/szukaj/' in body


class TestGlobalSearchResults:
    def test_no_query_shows_hint(self, staff_client):
        body = staff_client.get("/szukaj/").content.decode()
        assert "co najmniej 2 znaki" in body

    def test_single_character_query_shows_hint_and_finds_nothing(self, staff_client):
        make_event(title="Zażółć")
        body = staff_client.get("/szukaj/?q=Z").content.decode()
        assert "co najmniej 2 znaki" in body
        assert "Zażółć" not in body

    def test_finds_event_by_title(self, staff_client):
        event = make_event(title="Nocny Techno Rave")
        other = make_event(title="Coś innego")
        body = staff_client.get("/szukaj/?q=Techno").content.decode()
        assert event.title in body
        assert other.title not in body
        assert f"/admin/events/event/{event.pk}/change/" in body

    def test_finds_order_by_buyer_email(self, staff_client):
        event = make_event()
        pool = make_pool(event)
        order = make_order(
            event, pool, status=OrderStatus.PAID, buyer_email="unikalny.klient@example.com"
        )
        make_order(event, pool, buyer_email="ktos.inny@example.com")
        body = staff_client.get("/szukaj/?q=unikalny.klient").content.decode()
        assert order.short_id in body
        assert "unikalny.klient@example.com" in body
        assert f"/admin/orders/order/{order.pk}/change/" in body
        assert "ktos.inny@example.com" not in body

    def test_finds_order_by_short_id(self, staff_client):
        event = make_event()
        pool = make_pool(event)
        order = make_order(event, pool, status=OrderStatus.PAID)
        body = staff_client.get(f"/szukaj/?q={order.short_id}").content.decode()
        assert order.short_id in body
        assert order.buyer_email in body

    def test_finds_ticket_by_short_code(self, staff_client):
        event = make_event()
        pool = make_pool(event)
        order = make_order(event, pool, status=OrderStatus.PAID)
        ticket = make_ticket(order)
        body = staff_client.get(f"/szukaj/?q={ticket.short_code}").content.decode()
        assert ticket.short_code in body
        assert f"/admin/tickets/ticket/{ticket.pk}/change/" in body

    def test_finds_ticket_by_buyer_email(self, staff_client):
        event = make_event()
        pool = make_pool(event)
        order = make_order(
            event, pool, status=OrderStatus.PAID, buyer_email="bilet.klient@example.com"
        )
        ticket = make_ticket(order, buyer_email="bilet.klient@example.com")
        body = staff_client.get("/szukaj/?q=bilet.klient").content.decode()
        assert ticket.short_code in body

    def test_no_results_message(self, staff_client):
        body = staff_client.get("/szukaj/?q=nie-ma-takiego-nic").content.decode()
        assert "Brak wyników" in body

    def test_results_grouped_by_type(self, staff_client):
        event = make_event(title="Wspólny Fraszka Festival")
        pool = make_pool(event)
        order = make_order(event, pool, status=OrderStatus.PAID, buyer_email="fraszka@example.com")
        make_ticket(order, buyer_email="fraszka@example.com")
        body = staff_client.get("/szukaj/?q=Fraszka").content.decode()
        assert "Wydarzenia</h2>" in body
        assert "Zamówienia</h2>" in body
        assert "Bilety</h2>" in body

    def test_anonymous_user_redirected_to_login(self, client):
        response = client.get("/szukaj/?q=abc")
        assert response.status_code == 302


class TestGlobalSearchPermissions:
    def test_staff_without_order_view_permission_gets_403(self, client, django_user_model):
        django_user_model.objects.create_user("no_perms", password="x", is_staff=True)
        client.login(username="no_perms", password="x")
        assert client.get("/szukaj/?q=abc").status_code == 403
