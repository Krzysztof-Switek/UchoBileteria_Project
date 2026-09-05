import re

import pytest
from django.core import mail

from apps.orders.providers import demo
from apps.tickets.models import EmailOutbox, EmailStatus, Ticket, TicketStatus
from apps.tickets.sending import MAX_ATTEMPTS, send_pending_emails
from apps.tickets.services import hash_token, issue_tickets_for_order, qr_png_path
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


def paid_order(client, quantity=2):
    event = make_event()
    make_pool(event)
    client.post(f"/kup/{event.slug}/", {"buyer_email": "k@example.com", "quantity": quantity})
    from apps.orders.models import Order
    order = Order.objects.latest("created_at")
    client.post(f"/kasa-demo/{order.id}/zaplac/")
    order.refresh_from_db()
    return order


class TestTicketIssuing:
    def test_payment_issues_one_ticket_per_seat(self, client):
        order = paid_order(client, quantity=3)
        tickets = order.tickets.all()
        assert tickets.count() == 3
        for ticket in tickets:
            assert ticket.status == TicketStatus.ISSUED
            assert ticket.is_demo is True
            assert ticket.buyer_email == "k@example.com"

    def test_short_code_format_is_readable(self, client):
        order = paid_order(client, quantity=2)
        for ticket in order.tickets.all():
            assert re.fullmatch(r"[A-HJ-NP-Z2-9]{4}-[2-9]{2}", ticket.short_code), (
                ticket.short_code
            )

    def test_qr_token_stored_only_as_hash(self, client):
        order = paid_order(client, quantity=1)
        ticket = order.tickets.get()
        assert re.fullmatch(r"[0-9a-f]{64}", ticket.qr_token_hash)

    def test_qr_png_written_to_disk(self, client):
        order = paid_order(client, quantity=1)
        ticket = order.tickets.get()
        assert qr_png_path(ticket).exists()
        assert qr_png_path(ticket).stat().st_size > 0

    def test_duplicate_webhook_does_not_duplicate_tickets(self, client):
        order = paid_order(client, quantity=2)
        assert order.tickets.count() == 2
        body = demo.build_event_body("payment.succeeded", order, event_id="evt_again")
        demo.deliver(body, demo.sign(body))
        assert order.tickets.count() == 2

    def test_issue_is_idempotent_when_called_directly(self, client):
        order = paid_order(client, quantity=2)
        again = issue_tickets_for_order(order)
        assert len(again) == 2
        assert Ticket.objects.count() == 2

    def test_unpaid_order_gets_no_tickets(self):
        from apps.orders import services as order_services
        event = make_event()
        make_pool(event)
        order = order_services.create_order(event, "k@example.com", 1)
        assert issue_tickets_for_order(order) == []
        assert order.tickets.count() == 0

    def test_hash_token_matches_sha256(self):
        assert hash_token("abc") == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )


class TestTicketEmail:
    def test_email_sent_immediately_after_payment(
        self, client, django_capture_on_commit_callbacks
    ):
        """Live behaviour: the buyer's e-mail goes out right after payment."""
        with django_capture_on_commit_callbacks(execute=True):
            order = paid_order(client, quantity=2)
        item = EmailOutbox.objects.get(order=order)
        assert item.status == EmailStatus.SENT
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [order.buyer_email]
        assert len(mail.outbox[0].attachments) == 2  # QR codes attached

    def test_smtp_failure_leaves_item_for_cron_retry(
        self, client, django_capture_on_commit_callbacks, monkeypatch
    ):
        def boom(self):
            raise OSError("SMTP down")

        monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)
        with django_capture_on_commit_callbacks(execute=True):
            order = paid_order(client, quantity=1)
        item = EmailOutbox.objects.get(order=order)
        assert item.status == EmailStatus.PENDING  # cron will retry
        assert item.attempts == 1

        monkeypatch.undo()
        assert send_pending_emails() == 1

    def test_email_queued_after_payment(self, client):
        order = paid_order(client, quantity=2)
        item = EmailOutbox.objects.get(order=order)
        assert item.status == EmailStatus.PENDING
        assert item.to_email == "k@example.com"
        assert order.event.title in item.subject
        for ticket in order.tickets.all():
            assert ticket.short_code in item.body_html
            assert f"cid:qr-{ticket.id}" in item.body_html

    def test_send_pending_emails_delivers_with_qr_attachments(self, client):
        order = paid_order(client, quantity=2)
        sent = send_pending_emails()
        assert sent == 1
        assert len(mail.outbox) == 1
        message = mail.outbox[0]
        assert message.to == ["k@example.com"]
        # 2 QR images attached inline
        assert len(message.attachments) == 2
        item = EmailOutbox.objects.get(order=order)
        assert item.status == EmailStatus.SENT
        assert item.sent_at is not None

    def test_send_failure_keeps_pending_and_counts_attempts(self, client, monkeypatch):
        order = paid_order(client, quantity=1)

        def boom(self):
            raise OSError("SMTP down")

        monkeypatch.setattr(
            "django.core.mail.EmailMultiAlternatives.send", boom
        )
        assert send_pending_emails() == 0
        item = EmailOutbox.objects.get(order=order)
        assert item.status == EmailStatus.PENDING
        assert item.attempts == 1
        assert "SMTP down" in item.last_error

        monkeypatch.undo()
        assert send_pending_emails() == 1
        item.refresh_from_db()
        assert item.status == EmailStatus.SENT

    def test_email_fails_permanently_after_max_attempts(self, client, monkeypatch):
        order = paid_order(client, quantity=1)

        def boom(self):
            raise OSError("SMTP down")

        monkeypatch.setattr("django.core.mail.EmailMultiAlternatives.send", boom)
        for _ in range(MAX_ATTEMPTS):
            send_pending_emails()
        item = EmailOutbox.objects.get(order=order)
        assert item.status == EmailStatus.FAILED
        assert item.attempts == MAX_ATTEMPTS
        # no further attempts
        assert send_pending_emails() == 0


class TestPaidOrderPage:
    def test_paid_order_page_shows_tickets(self, client):
        order = paid_order(client, quantity=2)
        content = client.get(f"/zamowienie/{order.id}/").content.decode()
        for ticket in order.tickets.all():
            assert ticket.short_code in content

    def test_ticket_card_is_self_sufficient(self, client):
        # KUP-03: event name/date/venue/pool type on the same card as the QR,
        # not just a bare code — readable at the door without the site around it.
        order = paid_order(client, quantity=1)
        ticket = order.tickets.get()
        content = client.get(f"/zamowienie/{order.id}/").content.decode()
        assert order.event.title in content
        assert order.event.venue_name in content
        assert ticket.pool.name in content
        assert 'class="ticket-card bg-white' in content

    def test_single_ticket_order_has_no_carousel_controls(self, client):
        order = paid_order(client, quantity=1)
        content = client.get(f"/zamowienie/{order.id}/").content.decode()
        assert 'id="ticket-prev"' not in content

    def test_multi_ticket_order_shows_carousel_controls(self, client):
        # KUP-03: a counter + prev/next when there's more than one ticket.
        order = paid_order(client, quantity=3)
        content = client.get(f"/zamowienie/{order.id}/").content.decode()
        assert 'id="ticket-prev"' in content
        assert 'id="ticket-next"' in content
        assert "Bilet" in content and "z 3" in content

    def test_paid_order_offers_pdf_and_resend_actions(self, client):
        order = paid_order(client, quantity=1)
        content = client.get(f"/zamowienie/{order.id}/").content.decode()
        assert f"/zamowienie/{order.id}/bilety.pdf" in content
        assert "Pobierz PDF" in content
        assert f"/zamowienie/{order.id}/wyslij-ponownie/" in content
        assert "Wyślij ponownie e-mailem" in content


class TestTicketsPdfDownload:
    def test_downloads_a_pdf_for_a_paid_order(self, client):
        order = paid_order(client, quantity=2)
        response = client.get(f"/zamowienie/{order.id}/bilety.pdf")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF")
        assert f'filename="bilety-{order.short_id}.pdf"' in response["Content-Disposition"]

    def test_unpaid_order_redirects_instead_of_downloading(self, client):
        from apps.orders import services as order_services

        event = make_event()
        make_pool(event)
        order = order_services.create_order(event, "k@example.com", 1)
        response = client.get(f"/zamowienie/{order.id}/bilety.pdf", follow=True)
        assert response.status_code == 200  # followed redirect, not a PDF
        assert response["Content-Type"] != "application/pdf"
        assert "Dla tego zamówienia nie ma jeszcze biletów" in response.content.decode()


class TestResendTickets:
    def test_resend_queues_a_fresh_ticket_email(
        self, client, django_capture_on_commit_callbacks
    ):
        order = paid_order(client, quantity=2)
        mail.outbox.clear()
        with django_capture_on_commit_callbacks(execute=True):
            response = client.post(f"/zamowienie/{order.id}/wyslij-ponownie/")
        assert response.status_code == 302
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [order.buyer_email]
        assert EmailOutbox.objects.filter(order=order).count() == 2  # original + resend

    def test_resend_is_cooled_down_on_immediate_repeat(self, client, settings):
        settings.CACHES = {
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                        "LOCATION": "resend-cooldown-test"}
        }
        order = paid_order(client, quantity=1)
        client.post(f"/zamowienie/{order.id}/wyslij-ponownie/")
        response = client.post(f"/zamowienie/{order.id}/wyslij-ponownie/", follow=True)
        assert "wysłany przed chwilą" in response.content.decode()
        assert EmailOutbox.objects.filter(order=order).count() == 2  # original + first resend only

    def test_resend_rejected_for_unpaid_order(self, client):
        from apps.orders import services as order_services

        event = make_event()
        make_pool(event)
        order = order_services.create_order(event, "k@example.com", 1)
        response = client.post(f"/zamowienie/{order.id}/wyslij-ponownie/", follow=True)
        assert "nie ma jeszcze biletów" in response.content.decode()
        assert EmailOutbox.objects.filter(order=order).count() == 0

    def test_resend_requires_post(self, client):
        order = paid_order(client, quantity=1)
        assert client.get(f"/zamowienie/{order.id}/wyslij-ponownie/").status_code == 405
