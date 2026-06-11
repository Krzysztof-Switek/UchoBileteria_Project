import pytest

from apps.events import services
from apps.events.calendar import MAX_ATTEMPTS, process_calendar_outbox
from apps.events.models import CalendarAction, CalendarOutbox, EventStatus
from tests.factories import make_event, make_pool

pytestmark = pytest.mark.django_db


class FakeCalendarClient:
    """Test double recording calls; can be told to fail."""

    def __init__(self, fail=False):
        self.fail = fail
        self.upserts = []
        self.cancels = []

    def upsert_event(self, event):
        if self.fail:
            raise ConnectionError("Calendar API down")
        self.upserts.append(event)
        return event.calendar_event_id or f"gcal-{event.slug}"

    def cancel_event(self, event):
        if self.fail:
            raise ConnectionError("Calendar API down")
        self.cancels.append(event)


class TestEnqueue:
    def test_publish_enqueues_upsert(self):
        event = make_event(status=EventStatus.DRAFT)
        make_pool(event)
        services.publish_event(event)
        task = CalendarOutbox.objects.get()
        assert task.action == CalendarAction.UPSERT
        assert task.status == CalendarOutbox.Status.PENDING

    def test_cancel_enqueues_cancel(self):
        event = make_event()
        services.cancel_event(event)
        assert CalendarOutbox.objects.filter(action=CalendarAction.CANCEL).exists()

    def test_calendar_failure_never_blocks_publish(self):
        """Publishing only writes an outbox row — no API call, nothing to fail."""
        event = make_event(status=EventStatus.DRAFT)
        make_pool(event)
        services.publish_event(event)
        event.refresh_from_db()
        assert event.status == EventStatus.PUBLISHED


class TestProcessOutbox:
    def test_upsert_stores_calendar_event_id(self):
        event = make_event(status=EventStatus.DRAFT)
        make_pool(event)
        services.publish_event(event)
        client = FakeCalendarClient()
        summary = process_calendar_outbox(client)
        assert summary == {"done": 1, "failed": 0}
        event.refresh_from_db()
        assert event.calendar_event_id == f"gcal-{event.slug}"
        task = CalendarOutbox.objects.get()
        assert task.status == CalendarOutbox.Status.DONE

    def test_update_keeps_existing_calendar_id(self):
        event = make_event(calendar_event_id="gcal-istnieje")
        CalendarOutbox.objects.create(event=event, action=CalendarAction.UPSERT)
        client = FakeCalendarClient()
        process_calendar_outbox(client)
        event.refresh_from_db()
        assert event.calendar_event_id == "gcal-istnieje"

    def test_cancel_calls_client(self):
        event = make_event(calendar_event_id="gcal-x")
        CalendarOutbox.objects.create(event=event, action=CalendarAction.CANCEL)
        client = FakeCalendarClient()
        process_calendar_outbox(client)
        assert client.cancels == [event]

    def test_api_error_retries_then_fails_permanently(self):
        event = make_event()
        CalendarOutbox.objects.create(event=event, action=CalendarAction.UPSERT)
        failing = FakeCalendarClient(fail=True)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            summary = process_calendar_outbox(failing)
            task = CalendarOutbox.objects.get()
            assert task.attempts == attempt
            if attempt < MAX_ATTEMPTS:
                assert task.status == CalendarOutbox.Status.PENDING
                assert summary == {"done": 0, "failed": 0}
            else:
                assert task.status == CalendarOutbox.Status.FAILED
                assert summary == {"done": 0, "failed": 1}
        assert "Calendar API down" in task.last_error

        # a healthy client afterwards does not resurrect the failed task
        ok_client = FakeCalendarClient()
        assert process_calendar_outbox(ok_client) == {"done": 0, "failed": 0}
        assert ok_client.upserts == []

    def test_recovery_after_transient_error(self):
        event = make_event()
        CalendarOutbox.objects.create(event=event, action=CalendarAction.UPSERT)
        process_calendar_outbox(FakeCalendarClient(fail=True))
        summary = process_calendar_outbox(FakeCalendarClient())
        assert summary == {"done": 1, "failed": 0}
        event.refresh_from_db()
        assert event.calendar_event_id
