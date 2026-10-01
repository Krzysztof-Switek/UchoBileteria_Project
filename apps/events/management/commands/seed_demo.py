from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.roles import setup_roles
from apps.events.models import Event, EventStatus, TicketPool


class Command(BaseCommand):
    help = "Create a sample published event with three pools for demo testing."

    def handle(self, *args, **options):
        setup_roles()
        now = timezone.now()
        event, created = Event.objects.get_or_create(
            slug="test-koncert",
            defaults={
                "title": "Testowy koncert UCHO",
                "description": "Wydarzenie testowe do przeklikania trybu demo.",
                "venue_name": "Klub UCHO",
                "start_at": now + timedelta(days=14),
                "end_at": now + timedelta(days=14, hours=5),
                "sales_start_at": now - timedelta(hours=1),
                # Clamped by Event.save() to midnight before the concert day.
                "sales_end_at": now + timedelta(days=14),
                "capacity_total": 500,
                "status": EventStatus.PUBLISHED,
            },
        )
        if created:
            # Back-to-back windows (pools must not overlap); the last pool has
            # no end of its own and sells until the event's online cutoff.
            regular_from = now + timedelta(days=5)
            last_call_from = now + timedelta(days=10)
            for name, price, cap, start, end in [
                ("Early Bird", "40.00", 50, now - timedelta(hours=1), regular_from),
                ("Regular", "60.00", 350, regular_from, last_call_from),
                ("Last Call", "80.00", 100, last_call_from, None),
            ]:
                TicketPool.objects.create(
                    event=event,
                    name=name,
                    price_gross=Decimal(price),
                    capacity=cap,
                    sales_start_at=start,
                    sales_end_at=end,
                )
            self.stdout.write(self.style.SUCCESS(
                "Utworzono wydarzenie demo: /wydarzenia/test-koncert/"
            ))
        else:
            self.stdout.write("Wydarzenie demo już istnieje.")
