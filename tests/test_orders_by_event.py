"""Orders and the payment log are browsed per event; full lists on demand."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.orders.models import OrderStatus, PaymentEvent, PaymentEventStatus
from tests.factories import make_event, make_order, make_pool

pytestmark = pytest.mark.django_db

PICKER = "/admin/orders/order/wydarzenia/"


def event_with_order(title="Koncert A", days=10, **order_kwargs):
    event = make_event(title=title, start_at=timezone.now() + timedelta(days=days),
                       end_at=timezone.now() + timedelta(days=days, hours=3))
    order = make_order(event, make_pool(event), **order_kwargs)
    return event, order


class TestPicker:
    def test_bare_order_list_redirects_to_picker(self, admin_client):
        response = admin_client.get("/admin/orders/order/")
        assert response.status_code == 302
        assert response.url == PICKER

    def test_bare_payment_log_redirects_to_picker(self, admin_client):
        assert admin_client.get("/admin/orders/paymentevent/").url == PICKER

    def test_full_lists_on_demand(self, admin_client):
        assert admin_client.get("/admin/orders/order/?wszystkie=1").status_code == 200
        assert admin_client.get("/admin/orders/paymentevent/?wszystkie=1").status_code == 200

    def test_explicit_filter_still_works(self, admin_client):
        # e.g. the dashboard "Wymaga uwagi" links
        url = "/admin/orders/order/?status__exact=PAYMENT_PENDING"
        assert admin_client.get(url).status_code == 200

    def test_picker_lists_events_with_orders_and_links(self, admin_client):
        event, _ = event_with_order(status=OrderStatus.PAID)
        make_event(title="Bez zamówień")
        html = admin_client.get(PICKER).content.decode()
        assert "Koncert A" in html
        assert "Bez zamówień" not in html
        assert f"?event__id__exact={event.pk}" in html
        assert f"?order__event__id__exact={event.pk}" in html

    def test_picker_shows_empty_events_on_request(self, admin_client):
        make_event(title="Bez zamówień")
        assert "Bez zamówień" in admin_client.get(f"{PICKER}?puste=1").content.decode()

    def test_picker_search_by_name(self, admin_client):
        event_with_order("Jazz wieczór")
        event_with_order("Rock noc")
        html = admin_client.get(f"{PICKER}?q=jazz").content.decode()
        assert "Jazz wieczór" in html
        assert "Rock noc" not in html

    def test_picker_filter_by_month_and_quarter(self, admin_client):
        event, _ = event_with_order("Na miesiąc")
        month = timezone.localtime(event.start_at).month
        other_month = month % 12 + 1
        quarter = (month - 1) // 3 + 1
        assert "Na miesiąc" in admin_client.get(f"{PICKER}?miesiac={month}").content.decode()
        assert "Na miesiąc" not in admin_client.get(
            f"{PICKER}?miesiac={other_month}"
        ).content.decode()
        assert "Na miesiąc" in admin_client.get(f"{PICKER}?kwartal={quarter}").content.decode()

    def test_picker_flags_payment_log_entries_without_order(self, admin_client):
        PaymentEvent.objects.create(provider="DEMO", provider_event_id="x1", event_type="paid",
                                    processing_status=PaymentEventStatus.ERROR, is_demo=True)
        html = admin_client.get(PICKER).content.decode()
        assert "bez zamówienia" in html
        assert admin_client.get("/admin/orders/paymentevent/?order__isnull=True").status_code == 200


class TestPerEventLists:
    def test_order_list_for_event_titled_with_event(self, admin_client):
        event, order = event_with_order("Koncert B")
        _, other_order = event_with_order("Koncert C")
        url = f"/admin/orders/order/?event__id__exact={event.pk}"
        html = admin_client.get(url).content.decode()
        assert "Zamówienia — Koncert B" in html
        assert order.short_id in html
        assert other_order.short_id not in html

    def test_payment_log_for_event(self, admin_client):
        event, order = event_with_order("Koncert D")
        PaymentEvent.objects.create(provider="DEMO", provider_event_id="evt-d", event_type="paid",
                                    order=order, is_demo=True)
        url = f"/admin/orders/paymentevent/?order__event__id__exact={event.pk}"
        html = admin_client.get(url).content.decode()
        assert "Dziennik płatności — Koncert D" in html
        assert "evt-d" in html

    def test_event_page_links_to_its_orders_and_log(self, admin_client):
        event, _ = event_with_order()
        html = admin_client.get(f"/admin/events/event/{event.pk}/change/").content.decode()
        assert f"/admin/orders/order/?event__id__exact={event.pk}" in html
        assert f"/admin/orders/paymentevent/?order__event__id__exact={event.pk}" in html


class TestListLabelsAndDates:
    """Polish column headers and dd.mm.rr gg:mm dates in admin lists."""

    def test_order_list_headers_polish_and_short_dates(self, admin_client):
        from apps.admin_format import short_dt

        event, order = event_with_order("Koncert E")
        url = f"/admin/orders/order/?event__id__exact={event.pk}"
        html = admin_client.get(url).content.decode()
        for english in ("Short id", "Event", "Currency", "Created at"):
            assert f">{english}<" not in html
        for polish in ("Wydarzenie", "Waluta", "Utworzono"):
            assert polish in html
        assert short_dt(order.created_at) in html

    def test_other_lists_use_short_dates(self, admin_client):
        from apps.admin_format import short_dt
        from apps.auditlog.models import AuditLog

        entry = AuditLog.objects.create(action="test.action", entity_type="x", entity_id="1")
        html = admin_client.get("/admin/auditlog/auditlog/?wszystkie=1").content.decode()
        assert short_dt(entry.created_at) in html
        assert "Kiedy" in html


class TestTicketsPerEvent:
    def test_bare_ticket_list_redirects_to_picker(self, admin_client):
        assert admin_client.get("/admin/tickets/ticket/").url == PICKER

    def test_full_ticket_list_on_demand(self, admin_client):
        assert admin_client.get("/admin/tickets/ticket/?wszystkie=1").status_code == 200

    def test_ticket_list_for_event_titled_with_event(self, admin_client):
        event, _ = event_with_order("Koncert F")
        url = f"/admin/tickets/ticket/?event__id__exact={event.pk}"
        assert "Bilety — Koncert F" in admin_client.get(url).content.decode()

    def test_picker_and_event_page_link_to_event_tickets(self, admin_client):
        event, _ = event_with_order("Koncert G")
        link = f"/admin/tickets/ticket/?event__id__exact={event.pk}"
        assert link in admin_client.get(PICKER).content.decode()
        page = admin_client.get(f"/admin/events/event/{event.pk}/change/").content.decode()
        assert link in page


class TestAuditLogPerEvent:
    def test_log_action_links_entry_to_event(self):
        from apps.auditlog.services import log_action

        event, order = event_with_order("Koncert H")
        assert log_action("x.event", event).event == event
        assert log_action("x.order", order).event == event
        assert log_action("x.pool", order.pool).event == event

    def test_entry_without_event_stays_unlinked(self):
        from apps.auditlog.services import log_action
        from apps.orders.models import PaymentConfig

        assert log_action("x.config", PaymentConfig.load()).event is None

    def test_bare_audit_list_redirects_to_picker(self, admin_client):
        assert admin_client.get("/admin/auditlog/auditlog/").url == PICKER

    def test_audit_list_for_event(self, admin_client):
        from apps.auditlog.services import log_action

        event, order = event_with_order("Koncert I")
        other, _ = event_with_order("Koncert J")
        log_action("order.paid", order)
        log_action("event.published", other)
        url = f"/admin/auditlog/auditlog/?event__id__exact={event.pk}"
        html = admin_client.get(url).content.decode()
        assert "Dziennik zdarzeń — Koncert I" in html
        # rows only — the "akcja" filter sidebar lists every action type
        assert '<td class="field-action">order.paid</td>' in html
        assert '<td class="field-action">event.published</td>' not in html

    def test_picker_shows_event_with_only_audit_activity(self, admin_client):
        from apps.auditlog.services import log_action

        event = make_event(title="Tylko opublikowane")
        log_action("event.published", event)
        html = admin_client.get(PICKER).content.decode()
        assert "Tylko opublikowane" in html
        assert f"/admin/auditlog/auditlog/?event__id__exact={event.pk}" in html

    def test_unlinked_entries_reachable(self, admin_client):
        from apps.auditlog.services import log_action
        from apps.orders.models import PaymentConfig

        log_action("x.config", PaymentConfig.load())
        assert "niezwiązane z wydarzeniem" in admin_client.get(PICKER).content.decode()
        assert admin_client.get("/admin/auditlog/auditlog/?event__isnull=True").status_code == 200

    def test_event_page_links_to_its_audit_log(self, admin_client):
        event, _ = event_with_order()
        html = admin_client.get(f"/admin/events/event/{event.pk}/change/").content.decode()
        assert f"/admin/auditlog/auditlog/?event__id__exact={event.pk}" in html


class TestEmailsPerEvent:
    def make_email(self, order, **kwargs):
        from apps.tickets.models import EmailOutbox

        defaults = {"to_email": "a@b.pl", "subject": "Twoje bilety", "body_text": "x",
                    "order": order}
        defaults.update(kwargs)
        return EmailOutbox.objects.create(**defaults)

    def test_bare_email_list_redirects_to_picker(self, admin_client):
        assert admin_client.get("/admin/tickets/emailoutbox/").url == PICKER

    def test_failed_filter_from_dashboard_still_works(self, admin_client):
        url = "/admin/tickets/emailoutbox/?status__exact=FAILED"
        assert admin_client.get(url).status_code == 200

    def test_email_list_for_event(self, admin_client):
        event, order = event_with_order("Koncert K")
        _, other_order = event_with_order("Koncert L")
        self.make_email(order, subject="Bilety na K")
        self.make_email(other_order, subject="Bilety na L")
        url = f"/admin/tickets/emailoutbox/?order__event__id__exact={event.pk}"
        html = admin_client.get(url).content.decode()
        assert "E-maile — Koncert K" in html
        assert "Bilety na K" in html
        assert "Bilety na L" not in html

    def test_picker_counts_failed_emails(self, admin_client):
        from apps.tickets.models import EmailStatus

        event, order = event_with_order("Koncert M")
        self.make_email(order, status=EmailStatus.FAILED)
        html = admin_client.get(PICKER).content.decode()
        assert "1 z błędem" in html
        assert f"/admin/tickets/emailoutbox/?order__event__id__exact={event.pk}" in html

    def test_event_page_links_to_its_emails(self, admin_client):
        event, _ = event_with_order()
        html = admin_client.get(f"/admin/events/event/{event.pk}/change/").content.decode()
        assert f"/admin/tickets/emailoutbox/?order__event__id__exact={event.pk}" in html


class TestReportsPerEvent:
    def test_picker_links_to_event_report(self, admin_client):
        event, _ = event_with_order("Koncert N")
        html = admin_client.get(PICKER).content.decode()
        assert f"/raporty/wydarzenie/{event.pk}/" in html
        assert "raporty okresowe" in html  # aggregate reports stay reachable

    def test_reports_tile_goes_to_picker(self, admin_client):
        html = admin_client.get("/admin/").content.decode()
        tile = html.split("Raporty sprzedaży")[0].rsplit("<a ", 1)[1]
        assert f'href="{PICKER}"' in tile

    def test_event_report_links_back_to_picker(self, admin_client):
        event, _ = event_with_order()
        html = admin_client.get(f"/raporty/wydarzenie/{event.pk}/").content.decode()
        assert PICKER in html
        assert "Raporty okresowe" in html

    def test_nav_reports_link_goes_to_picker(self, admin_client):
        # both navs: Django admin pages and the Tailwind staff pages (reports)
        event, _ = event_with_order()
        for url in ("/admin/", f"/raporty/wydarzenie/{event.pk}/"):
            html = admin_client.get(url).content.decode()
            assert f'href="{PICKER}"' in html.split(">Raporty<")[0].rsplit("<a ", 1)[1], url
