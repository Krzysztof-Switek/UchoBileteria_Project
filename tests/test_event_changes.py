"""Changes to an event with sold tickets (06.10.2026): every change is shown for
confirmation before saving, and the person saving decides whether buyers get
an e-mail (date -> ticked by default, gate opening -> not)."""

from html.parser import HTMLParser

import pytest
from django.core import mail

from apps.orders.models import OrderStatus
from apps.tickets.models import EmailOutbox
from tests.factories import make_event, make_order, make_pool, make_ticket

pytestmark = pytest.mark.django_db


class _FormFields(HTMLParser):
    """Current values of the admin change form, as the browser would send them."""

    def __init__(self):
        super().__init__()
        self.data, self._select, self._textarea = {}, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        name = a.get("name")
        if tag == "input" and name and a.get("type") not in ("submit", "button", "file"):
            if a.get("type") in ("checkbox", "radio"):
                if "checked" in a:
                    self.data[name] = a.get("value", "on")
            else:
                self.data[name] = a.get("value", "")
        elif tag == "select" and name:
            self._select = name
        elif tag == "option" and self._select and "selected" in a:
            self.data[self._select] = a.get("value", "")
        elif tag == "textarea" and name:
            self._textarea = name
            self.data[name] = ""

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        if tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea:
            self.data[self._textarea] += data


def form_data(client, url):
    parser = _FormFields()
    parser.feed(client.get(url).content.decode())
    parser.data.pop("csrfmiddlewaretoken", None)
    return parser.data


@pytest.fixture
def admin(client, django_user_model):
    client.force_login(django_user_model.objects.create_superuser("adm", "a@k.pl", "x"))
    return client


def sold_event():
    event = make_event(title="Koncert ze sprzedażą")
    pool = make_pool(event)
    order = make_order(event, pool, status=OrderStatus.PAID, buyer_email="fan@example.com")
    make_ticket(order)
    return event


def test_date_change_waits_for_confirmation_then_mails_buyers(admin):
    event = sold_event()
    url = f"/admin/events/event/{event.pk}/change/"
    data = form_data(admin, url)
    data["start_at_1"] = "21:30"  # later start
    response = admin.post(url, data)
    html = response.content.decode()
    assert response.status_code == 200 and "potwierdź zmiany" in html
    assert 'name="_notify_buyers" value="1" checked' in html  # the date matters
    event.refresh_from_db()
    assert event.start_at.astimezone().strftime("%H:%M") != "21:30"  # not saved yet

    data.update({"_confirm_changes": "1", "_notify_buyers": "1", "_notify_note": "Przepraszamy"})
    response = admin.post(url, data)
    assert response.status_code == 302
    event.refresh_from_db()
    assert event.start_at.astimezone().strftime("%H:%M") == "21:30"
    sent = EmailOutbox.objects.filter(subject__startswith="Zmiany:")
    assert sent.count() == 1 and sent[0].to_email == "fan@example.com"
    assert "Przepraszamy" in sent[0].body_text and "21:30" in sent[0].body_text
    assert len(mail.outbox) <= 1


def test_gate_time_change_not_ticked_and_can_be_saved_without_mail(admin):
    event = sold_event()
    url = f"/admin/events/event/{event.pk}/change/"
    data = form_data(admin, url)
    gates = event.gates_open_at.astimezone().replace(minute=(event.gates_open_at.minute + 15) % 60)
    data["gates_open_at_1"] = gates.strftime("%H:%M")
    html = admin.post(url, data).content.decode()
    assert "potwierdź zmiany" in html
    assert 'name="_notify_buyers" value="1" checked' not in html
    data["_confirm_changes"] = "1"  # no _notify_buyers
    assert admin.post(url, data).status_code == 302
    assert not EmailOutbox.objects.filter(subject__startswith="Zmiany:").exists()


def test_event_without_sales_saves_straight_away(admin):
    event = make_event(title="Bez sprzedaży")
    make_pool(event)
    url = f"/admin/events/event/{event.pk}/change/"
    data = form_data(admin, url)
    data["title"] = "Nowy tytuł"
    assert admin.post(url, data).status_code == 302
    event.refresh_from_db()
    assert event.title == "Nowy tytuł"


def test_dashboard_event_list_has_edit_button(admin):
    event = sold_event()
    html = admin.get("/admin/").content.decode()
    edit_link = f'href="/admin/events/event/{event.pk}/change/" class="button" role="button">'
    assert edit_link + "Edytuj</a>" in html


def test_event_page_top_bar_no_sidebar_and_only_real_pools(admin):
    event = sold_event()
    html = admin.get(f"/admin/events/event/{event.pk}/change/").content.decode()
    assert 'class="ucho-event-topbar"' in html and 'id="nav-sidebar"' not in html
    assert "Odwołaj wydarzenie" in html and "Usuń wydarzenie" not in html  # has orders
    assert 'name="pools-TOTAL_FORMS" value="1"' in html  # no empty extra pool row
    assert admin.get(f"/admin/events/event/{event.pk}/delete/").status_code == 403


def test_event_without_orders_can_be_deleted(admin):
    event = make_event(title="Pomyłka")
    make_pool(event)
    html = admin.get(f"/admin/events/event/{event.pk}/change/").content.decode()
    assert "Usuń wydarzenie" in html
    assert admin.post(f"/admin/events/event/{event.pk}/delete/", {"post": "yes"}).status_code == 302


def test_pool_with_sold_tickets_cannot_be_deleted(admin):
    event = sold_event()
    pool = event.pools.get()
    url = f"/admin/events/event/{event.pk}/change/"
    data = form_data(admin, url)
    data["pools-0-DELETE"] = "on"
    data["_confirm_changes"] = "1"
    html = admin.post(url, data).content.decode()
    assert "Nie można usunąć puli „Regular” — sprzedano z niej" in html
    assert event.pools.filter(pk=pool.pk).exists()
    assert admin.get(f"/admin/events/ticketpool/{pool.pk}/delete/").status_code == 403
