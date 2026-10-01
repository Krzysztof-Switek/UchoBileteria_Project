"""Every staff screen has "Wstecz" + "Start" and a clickable path (2026-10-01)."""

import pytest
from django.contrib.auth.models import Group

from apps.accounts.roles import setup_roles
from tests.factories import make_event, make_order, make_pool

pytestmark = pytest.mark.django_db

PICKER = "/admin/orders/order/wydarzenia/"


def has_nav(html):
    return "data-nav-back" in html and "Wstecz</" in html


@pytest.fixture
def event_with_order():
    event = make_event(title="Koncert Nawigacja")
    order = make_order(event, make_pool(event))
    return event, order


@pytest.fixture
def door_client(client, django_user_model):
    setup_roles()
    user = django_user_model.objects.create_user("bramka", password="x")
    user.groups.add(Group.objects.get(name="DOOR_STAFF"))
    client.login(username="bramka", password="x")
    return client


class TestAdminScreens:
    @pytest.mark.parametrize("url", [
        "/admin/events/event/", "/admin/events/event/add/", PICKER,
        "/admin/tickets/emailoutbox/?wszystkie=1",
    ])
    def test_back_and_home_on_admin_pages(self, admin_client, url):
        html = admin_client.get(url).content.decode()
        assert has_nav(html), url
        assert 'title="Pulpit"' in html

    def test_dashboard_itself_has_no_path(self, admin_client):
        # home page: nowhere further up to go
        assert "data-nav-back" not in admin_client.get("/admin/").content.decode()

    def test_per_event_list_path_goes_through_event(self, admin_client, event_with_order):
        event, _ = event_with_order
        url = f"/admin/orders/order/?event__id__exact={event.pk}"
        html = admin_client.get(url).content.decode()
        crumbs = html.split('class="breadcrumbs"')[1].split("</div>")[0]
        assert f'href="{PICKER}"' in crumbs
        assert f'href="/admin/events/event/{event.pk}/change/"' in crumbs
        assert "Zamówienia" in crumbs

    def test_record_path_links_back_to_event_list(self, admin_client, event_with_order):
        event, order = event_with_order
        html = admin_client.get(f"/admin/orders/order/{order.pk}/change/").content.decode()
        crumbs = html.split('class="breadcrumbs"')[1].split("</div>")[0]
        assert f'href="/admin/orders/order/?event__id__exact={event.pk}"' in crumbs
        assert f'href="/admin/events/event/{event.pk}/change/"' in crumbs

    def test_ticket_record_path(self, admin_client, event_with_order):
        from apps.tickets.services import issue_tickets_for_order

        event, order = event_with_order
        order.status = "PAID"
        order.save()
        ticket = issue_tickets_for_order(order)[0]
        html = admin_client.get(f"/admin/tickets/ticket/{ticket.pk}/change/").content.decode()
        crumbs = html.split('class="breadcrumbs"')[1].split("</div>")[0]
        assert f'href="/admin/tickets/ticket/?event__id__exact={event.pk}"' in crumbs


class TestReportScreens:
    def test_event_report_path(self, admin_client, event_with_order):
        event, _ = event_with_order
        html = admin_client.get(f"/raporty/wydarzenie/{event.pk}/").content.decode()
        assert has_nav(html)
        crumbs = html.split('class="ucho-crumbs')[1].split("</nav>")[0]
        assert f'href="{PICKER}"' in crumbs
        assert f'href="/admin/events/event/{event.pk}/change/"' in crumbs

    @pytest.mark.parametrize("url", ["/raporty/", "/szukaj/?q=x"])
    def test_other_report_pages_have_nav(self, admin_client, url):
        assert has_nav(admin_client.get(url).content.decode())


class TestGateScreens:
    def test_door_staff_home_is_the_gate(self, door_client):
        html = door_client.get("/wejscie/").content.decode()
        assert has_nav(html)
        assert '<a href="/wejscie/" title="Start"' in html
        assert "/admin/" not in html.split('class="ucho-crumbs')[1].split("</nav>")[0]

    def test_staff_home_on_gate_is_dashboard(self, admin_client):
        html = admin_client.get("/wejscie/").content.decode()
        assert '<a href="/admin/" title="Start"' in html

    def test_scanner_path_and_search_back_to_scanner(self, door_client):
        event = make_event(title="Koncert Bramka")
        make_pool(event)
        html = door_client.get(f"/wejscie/{event.id}/skaner/").content.decode()
        assert has_nav(html)
        assert "Koncert Bramka" in html.split('class="ucho-crumbs')[1].split("</nav>")[0]
        search = door_client.get(f"/wejscie/{event.id}/szukaj/").content.decode()
        crumbs = search.split('class="ucho-crumbs')[1].split("</nav>")[0]
        assert f'href="/wejscie/{event.id}/skaner/"' in crumbs
