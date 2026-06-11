from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.checkin.services import write_emergency_list
from apps.events.models import Event, EventStatus


class Command(BaseCommand):
    help = (
        "Write an emergency check-in CSV for every event starting within the "
        "next N hours (default 2). Run via cron."
    )

    def add_arguments(self, parser):
        parser.add_argument("--hours", type=int, default=2)

    def handle(self, *args, **options):
        now = timezone.now()
        window_end = now + timedelta(hours=options["hours"])
        events = Event.objects.filter(
            status=EventStatus.PUBLISHED, start_at__gte=now, start_at__lte=window_end
        )
        out_dir = Path(settings.MEDIA_ROOT) / "emergency"
        out_dir.mkdir(parents=True, exist_ok=True)
        for event in events:
            stamp = timezone.localtime(now).strftime("%Y%m%d-%H%M")
            path = out_dir / f"{event.slug}-{stamp}.csv"
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                count = write_emergency_list(event, fh)
            self.stdout.write(self.style.SUCCESS(f"{event.slug}: {count} biletów -> {path}"))
        if not events:
            self.stdout.write("Brak wydarzeń w oknie czasowym.")
