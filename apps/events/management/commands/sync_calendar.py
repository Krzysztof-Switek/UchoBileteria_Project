from django.core.management.base import BaseCommand

from apps.events.calendar import (
    GoogleCalendarClient,
    calendar_configured,
    process_calendar_outbox,
)


class Command(BaseCommand):
    help = "Process pending Google Calendar sync tasks (run via cron)."

    def handle(self, *args, **options):
        if not calendar_configured():
            self.stdout.write(
                "Google Calendar nie jest skonfigurowany "
                "(GOOGLE_CALENDAR_ID / GOOGLE_SERVICE_ACCOUNT_FILE) — pomijam."
            )
            return
        summary = process_calendar_outbox(GoogleCalendarClient())
        self.stdout.write(
            self.style.SUCCESS(
                f"Calendar sync: {summary['done']} ok, {summary['failed']} failed."
            )
        )
