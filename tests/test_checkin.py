import io
import threading

import pytest
from django.db import connection

from apps.checkin.services import (
    ScanResult,
    check_in_by_short_code,
    check_in_by_token,
    write_emergency_list,
)
from apps.tickets.models import TicketStatus
from tests.factories import make_event, make_order, make_pool, make_ticket

pytestmark = pytest.mark.django_db


def staff_user(django_user_model, username="bramkarz"):
    return django_user_model.objects.create_user(username, password="x")


def ticket_with_token(event=None, status=TicketStatus.ISSUED):
    event = event or make_event()
    pool = make_pool(event)
    order = make_order(event, pool)
    token = "tok-" + event.slug
    ticket = make_ticket(order, raw_token=token, status=status)
    return event, ticket, token


class TestCheckInService:
    def test_valid_scan_checks_in(self, django_user_model):
        staff = staff_user(django_user_model)
        event, ticket, token = ticket_with_token()
        result, found = check_in_by_token(token, event, staff)
        assert result == ScanResult.VALID_CHECKED_IN
        ticket.refresh_from_db()
        assert ticket.status == TicketStatus.CHECKED_IN
        assert ticket.checked_in_at is not None
        assert ticket.checked_in_by == staff

    def test_second_scan_rejected(self):
        event, ticket, token = ticket_with_token()
        check_in_by_token(token, event)
        result, _ = check_in_by_token(token, event)
        assert result == ScanResult.ALREADY_CHECKED_IN

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (TicketStatus.REFUNDED, ScanResult.REFUNDED),
            (TicketStatus.CANCELLED, ScanResult.CANCELLED),
            (TicketStatus.INVALIDATED, ScanResult.CANCELLED),
        ],
    )
    def test_invalid_states_rejected(self, status, expected):
        event, ticket, token = ticket_with_token(status=status)
        result, _ = check_in_by_token(token, event)
        assert result == expected
        ticket.refresh_from_db()
        assert ticket.status == status  # unchanged

    def test_wrong_event(self):
        event, ticket, token = ticket_with_token()
        other_event = make_event()
        result, _ = check_in_by_token(token, other_event)
        assert result == ScanResult.WRONG_EVENT
        ticket.refresh_from_db()
        assert ticket.status == TicketStatus.ISSUED

    def test_unknown_token(self):
        event = make_event()
        result, found = check_in_by_token("nie-istnieje", event)
        assert result == ScanResult.INVALID_TOKEN
        assert found is None

    def test_check_in_by_short_code_case_insensitive(self):
        event, ticket, _ = ticket_with_token()
        result, _ = check_in_by_short_code(ticket.short_code.lower(), event)
        assert result == ScanResult.VALID_CHECKED_IN


@pytest.mark.django_db(transaction=True)
class TestConcurrentScan:
    def test_simultaneous_double_scan_admits_exactly_once(self):
        event, ticket, token = ticket_with_token()
        results = []
        barrier = threading.Barrier(2)

        def scan():
            try:
                barrier.wait(timeout=10)
                result, _ = check_in_by_token(token, event)
                results.append(result)
            finally:
                connection.close()

        threads = [threading.Thread(target=scan) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert sorted(results) == sorted(
            [ScanResult.VALID_CHECKED_IN, ScanResult.ALREADY_CHECKED_IN]
        ), results


class TestScanViews:
    def login(self, client, django_user_model):
        staff_user(django_user_model)
        client.login(username="bramkarz", password="x")

    def test_scan_endpoint_requires_login(self, client):
        event, ticket, token = ticket_with_token()
        response = client.post(f"/wejscie/{event.id}/skan/", {"code": token})
        assert response.status_code == 302
        assert "/logowanie/" in response.url

    def test_scan_endpoint_accepts_verify_url(self, client, django_user_model):
        self.login(client, django_user_model)
        event, ticket, token = ticket_with_token()
        scanned = f"http://localhost:8000/wejscie/weryfikuj?t={token}"
        response = client.post(f"/wejscie/{event.id}/skan/", {"code": scanned})
        data = response.json()
        assert data["ok"] is True
        assert data["result"] == ScanResult.VALID_CHECKED_IN
        assert data["ticket_code"] == ticket.short_code

    def test_scan_endpoint_accepts_short_code(self, client, django_user_model):
        self.login(client, django_user_model)
        event, ticket, _ = ticket_with_token()
        response = client.post(f"/wejscie/{event.id}/skan/", {"code": ticket.short_code})
        assert response.json()["ok"] is True

    def test_scan_rejection_payload(self, client, django_user_model):
        self.login(client, django_user_model)
        event, ticket, token = ticket_with_token(status=TicketStatus.REFUNDED)
        response = client.post(f"/wejscie/{event.id}/skan/", {"code": token})
        data = response.json()
        assert data["ok"] is False
        assert data["result"] == ScanResult.REFUNDED

    def test_search_page_lists_tickets(self, client, django_user_model):
        self.login(client, django_user_model)
        event, ticket, _ = ticket_with_token()
        response = client.get(f"/wejscie/{event.id}/szukaj/", {"q": ticket.buyer_email})
        content = response.content.decode()
        assert ticket.short_code in content
        assert "Wpuść" in content

    def test_manual_check_in_from_search(self, client, django_user_model):
        self.login(client, django_user_model)
        event, ticket, _ = ticket_with_token()
        response = client.post(
            f"/wejscie/{event.id}/odpraw/", {"code": ticket.short_code}
        )
        assert "Bilet ważny" in response.content.decode()
        ticket.refresh_from_db()
        assert ticket.status == TicketStatus.CHECKED_IN


class TestEmergencyList:
    def test_csv_contains_required_columns(self):
        event, ticket, _ = ticket_with_token()
        used = make_ticket(make_order(event, make_pool(event, priority=2)),
                           status=TicketStatus.CHECKED_IN)
        buffer = io.StringIO()
        count = write_emergency_list(event, buffer)
        assert count == 2
        content = buffer.getvalue()
        assert "Kod biletu" in content
        assert ticket.short_code in content
        assert used.short_code in content
        assert ticket.buyer_email in content
        assert event.title in content

    def test_csv_export_view_requires_login(self, client):
        event, *_ = ticket_with_token()
        response = client.get(f"/wejscie/{event.id}/lista-awaryjna.csv")
        assert response.status_code == 302

    def test_csv_export_view(self, client, django_user_model):
        staff_user(django_user_model)
        client.login(username="bramkarz", password="x")
        event, ticket, _ = ticket_with_token()
        response = client.get(f"/wejscie/{event.id}/lista-awaryjna.csv")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert ticket.short_code in response.content.decode("utf-8-sig")

    def test_snapshot_command_writes_files(self, settings, tmp_path):
        from datetime import timedelta

        from django.core.management import call_command
        from django.utils import timezone

        settings.MEDIA_ROOT = tmp_path
        event = make_event(
            start_at=timezone.now() + timedelta(hours=1),
            end_at=timezone.now() + timedelta(hours=5),
        )
        make_ticket(make_order(event, make_pool(event)))
        call_command("snapshot_emergency_lists")
        files = list((tmp_path / "emergency").glob("*.csv"))
        assert len(files) == 1
