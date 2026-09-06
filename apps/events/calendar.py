"""
Google Calendar integration (spec section 18) — strictly non-blocking.

Publishing/cancelling an event only enqueues a sync task; the actual API
call happens in the `sync_calendar` cron command. A Calendar outage can
therefore never block ticket sales or event publication.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.auditlog.services import log_action

from .models import CalendarAction, CalendarOutbox, Event

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def enqueue_calendar_sync(event: Event, action: str) -> CalendarOutbox:
    return CalendarOutbox.objects.create(event=event, action=action)


class GoogleCalendarClient:
    """Thin wrapper over the Google Calendar API (built lazily from settings)."""

    def __init__(self):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_SERVICE_ACCOUNT_FILE,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        self.calendar_id = settings.GOOGLE_CALENDAR_ID
        self.api = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def _body(self, event: Event) -> dict:
        url = f"{settings.SITE_BASE_URL}/wydarzenia/{event.slug}/"
        return {
            "summary": event.title,
            "location": f"{event.venue_name}, {event.venue_address}".strip(", "),
            "description": f"{event.description[:500]}\n\nBilety: {url}",
            "start": {"dateTime": event.start_at.isoformat()},
            # end_at is optional (may not be known yet) — Calendar requires an
            # end time regardless, so estimate one instead of leaving it out.
            "end": {"dateTime": (event.end_at or event.start_at + timedelta(hours=4)).isoformat()},
        }

    def upsert_event(self, event: Event) -> str:
        if event.calendar_event_id:
            self.api.events().patch(
                calendarId=self.calendar_id,
                eventId=event.calendar_event_id,
                body=self._body(event),
            ).execute()
            return event.calendar_event_id
        created = (
            self.api.events()
            .insert(calendarId=self.calendar_id, body=self._body(event))
            .execute()
        )
        return created["id"]

    def cancel_event(self, event: Event) -> None:
        if not event.calendar_event_id:
            return
        self.api.events().delete(
            calendarId=self.calendar_id, eventId=event.calendar_event_id
        ).execute()


def calendar_configured() -> bool:
    return bool(
        getattr(settings, "GOOGLE_CALENDAR_ID", "")
        and getattr(settings, "GOOGLE_SERVICE_ACCOUNT_FILE", "")
    )


def process_calendar_outbox(client, limit: int = 20) -> dict:
    """Run pending sync tasks against the given client. Never raises."""
    pending = CalendarOutbox.objects.filter(
        status=CalendarOutbox.Status.PENDING, attempts__lt=MAX_ATTEMPTS
    ).select_related("event").order_by("created_at")[:limit]

    done = failed = 0
    for task in pending:
        task.attempts += 1
        try:
            if task.action == CalendarAction.UPSERT:
                calendar_event_id = client.upsert_event(task.event)
                if calendar_event_id != task.event.calendar_event_id:
                    Event.objects.filter(pk=task.event_id).update(
                        calendar_event_id=calendar_event_id
                    )
            else:
                client.cancel_event(task.event)
        except Exception as exc:  # noqa: BLE001 - calendar failures must not break the queue
            logger.warning("Calendar sync %s failed (attempt %s): %s",
                           task.pk, task.attempts, exc)
            task.last_error = str(exc)[:2000]
            if task.attempts >= MAX_ATTEMPTS:
                task.status = CalendarOutbox.Status.FAILED
                failed += 1
            task.save(update_fields=["attempts", "last_error", "status"])
            continue
        task.status = CalendarOutbox.Status.DONE
        task.processed_at = timezone.now()
        task.last_error = ""
        task.save(update_fields=["attempts", "status", "processed_at", "last_error"])
        log_action("calendar.synced", task.event, metadata={"action": task.action})
        done += 1
    return {"done": done, "failed": failed}
