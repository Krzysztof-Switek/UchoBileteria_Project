"""Cancelling an event (06.10.2026): every buyer is told; without a new date the
money goes back at once (to the original payment method, via the operator);
with a new date the buyer keeps the tickets or takes the refund, no answer by
the deadline = automatic refund. Every step lands in the audit log."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.auditlog.models import AuditLog
from apps.events.models import EventStatus
from apps.orders import cancellation
from apps.orders.models import CancellationStatus, Order, OrderStatus
from apps.tickets.models import EmailOutbox, TicketStatus
from tests.factories import make_event, make_pool
from tests.test_refunds_reports import paid_demo_order

pytestmark = pytest.mark.django_db


def actions_of(order):
    return list(AuditLog.objects.filter(entity_id=str(order.pk)).values_list("action", flat=True))


@pytest.fixture
def two_dates():
    old = make_event(title="Koncert (stary termin)")
    make_pool(old, capacity=50)
    new = make_event(title="Koncert (nowy termin)", start_at=timezone.now() + timedelta(days=40))
    make_pool(new, capacity=50)
    return old, new


def test_without_new_date_everyone_is_refunded_and_told(client):
    event = make_event()
    make_pool(event, capacity=50)
    _, order = paid_demo_order(client, event=event, email="a@example.com")
    result = cancellation.cancel_event_for_buyers(event, reason="Choroba wokalisty")
    assert result["notified"] == 1 and result["refunded"] == 1
    order.refresh_from_db()
    event.refresh_from_db()
    assert event.status == EventStatus.CANCELLED and event.cancelled_at
    assert order.status == OrderStatus.REFUNDED
    assert order.cancellation_status == CancellationStatus.REFUND_REQUESTED
    mail = EmailOutbox.objects.get(subject__startswith="Wydarzenie odwołane")
    assert "Choroba wokalisty" in mail.body_text and "ZWROT: " in mail.body_text
    assert {"cancellation.notified", "cancellation.refund_requested"} <= set(actions_of(order))


def test_buyer_keeps_tickets_for_the_new_date(client, two_dates):
    old, new = two_dates
    _, order = paid_demo_order(client, event=old, quantity=2)
    codes = sorted(order.tickets.values_list("short_code", flat=True))
    cancellation.cancel_event_for_buyers(old, replacement=new)
    order.refresh_from_db()
    assert order.cancellation_status == CancellationStatus.AWAITING
    mail = EmailOutbox.objects.get(subject__startswith="Zmiana terminu")
    assert f"/zamowienie/{order.pk}/odwolanie/" in mail.body_text

    page = client.get(f"/zamowienie/{order.pk}/odwolanie/").content.decode()
    assert "Zachowuję bilety na nowy termin" in page
    client.post(f"/zamowienie/{order.pk}/odwolanie/", {"wybor": "nowy-termin"})
    order.refresh_from_db()
    old.refresh_from_db()
    new.refresh_from_db()
    assert order.cancellation_status == CancellationStatus.MOVED
    assert order.event == new and order.moved_from_event == old
    assert sorted(order.tickets.values_list("short_code", flat=True)) == codes
    assert set(order.tickets.values_list("event", flat=True)) == {new.pk}
    assert (old.sold_total, new.sold_total) == (0, 2)
    assert order.status == OrderStatus.PAID
    assert "cancellation.moved" in actions_of(order)


def test_buyer_chooses_refund(client, two_dates):
    old, new = two_dates
    _, order = paid_demo_order(client, event=old)
    cancellation.cancel_event_for_buyers(old, replacement=new)
    client.post(f"/zamowienie/{order.pk}/odwolanie/", {"wybor": "zwrot"})
    order.refresh_from_db()
    assert order.status == OrderStatus.REFUNDED
    assert order.cancellation_status == CancellationStatus.REFUND_REQUESTED
    assert "cancellation.buyer_chose_refund" in actions_of(order)


def test_no_answer_by_deadline_means_refund(client, two_dates):
    old, new = two_dates
    _, order = paid_demo_order(client, event=old)
    deadline = timezone.now() + timedelta(days=8)
    cancellation.cancel_event_for_buyers(old, replacement=new, deadline=deadline)
    assert cancellation.process_decision_deadlines()["refunded"] == 0
    result = cancellation.process_decision_deadlines(now=deadline + timedelta(hours=1))
    assert result["refunded"] == 1
    order.refresh_from_db()
    assert order.status == OrderStatus.REFUNDED
    assert {"cancellation.deadline_passed", "cancellation.refund_requested"} <= set(
        actions_of(order))


def test_failed_refund_is_marked_for_manual_handling(client, monkeypatch):
    event = make_event()
    make_pool(event, capacity=50)
    _, order = paid_demo_order(client, event=event)

    def boom(*args, **kwargs):
        raise RuntimeError("operator odrzucił zwrot")

    monkeypatch.setattr(cancellation, "refund_order", boom)
    result = cancellation.cancel_event_for_buyers(event)
    assert result["refund_failed"] == 1
    order.refresh_from_db()
    assert order.cancellation_status == CancellationStatus.REFUND_FAILED
    log = AuditLog.objects.get(entity_id=str(order.pk), action="cancellation.refund_failed")
    assert "operator odrzucił" in log.metadata["error"]


def test_admin_cancel_page_and_plain_action_refused_with_buyers(client, django_user_model,
                                                                two_dates):
    old, new = two_dates
    _, order = paid_demo_order(client, event=old)
    admin = django_user_model.objects.create_superuser("adm", "a@k.pl", "x")
    client.force_login(admin)
    # the bulk "Odwołaj" without notifying is refused when tickets are sold
    client.post("/admin/events/event/", {"action": "cancel_events",
                                         "_selected_action": [old.pk]})
    old.refresh_from_db()
    assert old.status != EventStatus.CANCELLED
    page = client.get(f"/admin/events/event/{old.pk}/odwolaj/").content.decode()
    assert new.title in page
    response = client.post(f"/admin/events/event/{old.pk}/odwolaj/", {
        "reason": "Przeniesienie", "mode": "nowy-termin", "replacement": new.pk, "confirm": "1",
        "deadline": (timezone.now() + timedelta(days=10)).strftime("%Y-%m-%d"),
    })
    assert response.status_code == 302
    old.refresh_from_db()
    assert old.status == EventStatus.CANCELLED and old.replacement_event == new
    status = client.get(f"/admin/events/event/{old.pk}/odwolanie/").content.decode()
    assert order.buyer_email in status and "Czeka na decyzję" in status


def test_tickets_of_moved_order_are_valid_for_new_date(client, two_dates):
    old, new = two_dates
    _, order = paid_demo_order(client, event=old)
    cancellation.cancel_event_for_buyers(old, replacement=new)
    cancellation.move_to_replacement(order)
    assert all(t.status == TicketStatus.ISSUED and t.event_id == new.pk
               for t in Order.objects.get(pk=order.pk).tickets.all())


def test_decision_deadline_rules(two_dates):
    from django.core.exceptions import ValidationError

    old, new = two_dates
    with pytest.raises(ValidationError, match="co najmniej 7 dni"):
        cancellation.cancel_event_for_buyers(
            old, replacement=new, deadline=timezone.now() + timedelta(days=2))
    with pytest.raises(ValidationError, match="dzień przed"):
        cancellation.cancel_event_for_buyers(
            old, replacement=new, deadline=new.start_at)
    old.refresh_from_db()
    assert old.status != EventStatus.CANCELLED  # nothing happened


def test_purchase_requires_accepting_terms(client):
    event = make_event()
    make_pool(event)
    client.post(f"/kup/{event.slug}/", {"buyer_email": "x@example.com", "quantity": 1})
    assert not Order.objects.exists()
    client.post(f"/kup/{event.slug}/", {"buyer_email": "x@example.com", "quantity": 1,
                                        "accept_terms": "on"})
    order = Order.objects.get()
    assert order.terms_version and order.terms_accepted_at
    page = client.get("/regulamin/").content.decode()
    assert order.terms_version in page and "nigdy nie przepada" in page


def test_manager_refunds_awaiting_buyers_now(client, django_user_model, two_dates):
    old, new = two_dates
    _, waiting = paid_demo_order(client, event=old, email="czeka@example.com")
    _, mover = paid_demo_order(client, event=old, email="przenosi@example.com")
    cancellation.cancel_event_for_buyers(old, replacement=new)
    cancellation.move_to_replacement(mover)
    client.force_login(django_user_model.objects.create_superuser("adm", "a@k.pl", "x"))
    url = f"/admin/events/event/{old.pk}/odwolanie/"
    assert "Zwróć teraz wszystkim czekającym" in client.get(url).content.decode()
    client.post(url, {})  # without the confirmation nothing happens
    waiting.refresh_from_db()
    assert waiting.cancellation_status == CancellationStatus.AWAITING
    client.post(url, {"confirm": "1"})
    waiting.refresh_from_db()
    mover.refresh_from_db()
    assert waiting.status == OrderStatus.REFUNDED
    assert mover.status == OrderStatus.PAID and mover.event == new  # moved one untouched
    assert "cancellation.manager_refund" in actions_of(waiting)


def test_default_text_goes_out_when_manager_changes_nothing(client, two_dates):
    old, new = two_dates
    _, order = paid_demo_order(client, event=old)
    cancellation.cancel_event_for_buyers(old, replacement=new)  # no reason given
    old.refresh_from_db()
    assert old.cancellation_reason == cancellation.default_message(old, new)
    mail = EmailOutbox.objects.get(subject__startswith="Zmiana terminu")
    assert "zmienia termin" in mail.body_text and "Na decyzję masz czas do" in mail.body_text


def test_cancel_page_shows_amount_tickets_calendar_and_editable_text(client, django_user_model,
                                                                     two_dates):
    old, new = two_dates
    paid_demo_order(client, event=old, quantity=2)
    client.force_login(django_user_model.objects.create_superuser("adm", "a@k.pl", "x"))
    html = client.get(f"/admin/events/event/{old.pk}/odwolaj/").content.decode()
    assert "Do zwrotu" in html and "Opłacone zamówienia" not in html
    assert 'id="cal-data"' in html and new.title in html
    assert "Z przykrością informujemy" in html  # pre-filled, editable e-mail text
    # "new date" chosen but no event picked in the calendar -> refused
    response = client.post(f"/admin/events/event/{old.pk}/odwolaj/",
                           {"mode": "nowy-termin", "confirm": "1", "reason": "x"})
    assert "Kliknij w kalendarzu dzień" in response.content.decode()


def test_evidence_page_and_gate_show_the_proof(client, django_user_model, two_dates):
    from apps.tickets.models import Ticket

    old, new = two_dates
    _, order = paid_demo_order(client, event=old)
    cancellation.cancel_event_for_buyers(
        old, replacement=new, deadline=timezone.now() + timedelta(days=8))
    cancellation.process_decision_deadlines(now=timezone.now() + timedelta(days=9))
    admin = django_user_model.objects.create_superuser("adm", "a@k.pl", "x")
    client.force_login(admin)
    page = client.get(f"/admin/orders/order/{order.pk}/historia/").content.decode()
    assert "Wysłano informację o odwołaniu" in page and "E-mail: Zmiana terminu" in page
    assert "Minął termin decyzji" in page and "Zwrot zlecony operatorowi" in page
    # the buyer missed everything and turns up at the new date with the old ticket
    ticket = Ticket.objects.filter(order=order).first()
    data = client.post(f"/wejscie/{new.id}/skan/", {"code": ticket.short_code}).json()
    assert data["result"] == "REFUNDED" and "Dowód: zwrot" in data["message"]


def test_postpone_by_clicking_a_day_creates_the_new_date_and_sends_no_cancel_word(
        client, django_user_model):
    event = make_event(title="Koncert jesienny")
    make_pool(event, capacity=50, name="Regular")
    _, order = paid_demo_order(client, event=event)
    client.force_login(django_user_model.objects.create_superuser("adm", "a@k.pl", "x"))
    day = (timezone.localtime() + timedelta(days=30)).strftime("%Y-%m-%d")
    response = client.post(f"/admin/events/event/{event.pk}/odwolaj/", {
        "mode": "nowy-termin", "new_date": day, "new_time": "19:30", "confirm": "1",
        "reason": "",
    })
    assert response.status_code == 302
    event.refresh_from_db()
    new = event.replacement_event
    assert new is not None and new.title == "Koncert jesienny"
    assert new.status == EventStatus.PUBLISHED
    assert timezone.localtime(new.start_at).strftime("%Y-%m-%d %H:%M") == f"{day} 19:30"
    assert list(new.pools.values_list("name", "sold_count")) == [("Regular", 0)]
    mail = EmailOutbox.objects.get(order=order, subject__startswith="Zmiana terminu")
    assert "odwoł" not in mail.subject.lower() and "odwoł" not in mail.body_text.lower()
    assert "Potwierdź wybór" in mail.body_text
    page = client.get(f"/wydarzenia/{event.slug}/").content.decode()
    assert "Zmiana terminu" in page and "Wydarzenie odwołane" not in page


def test_plain_cancellation_mail_says_cancelled(client):
    event = make_event()
    make_pool(event, capacity=50)
    _, order = paid_demo_order(client, event=event)
    cancellation.cancel_event_for_buyers(event)
    mail = EmailOutbox.objects.get(order=order, subject__startswith="Wydarzenie odwołane")
    assert "Wydarzenie odwołane" in mail.body_text and "Zmiana terminu" not in mail.body_text
