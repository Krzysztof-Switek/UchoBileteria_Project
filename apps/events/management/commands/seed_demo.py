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
                "sales_end_at": now + timedelta(days=14),
                "capacity_total": 500,
                "status": EventStatus.PUBLISHED,
            },
        )
        if created:
            for name, priority, price, cap, start in [
                ("Early Bird", 1, "40.00", 50, None),
                ("Regular", 2, "60.00", 350, now + timedelta(days=5)),
                ("Last Call", 3, "80.00", 100, now + timedelta(days=10)),
            ]:
                TicketPool.objects.create(
                    event=event,
                    name=name,
                    priority=priority,
                    price_gross=Decimal(price),
                    capacity=cap,
                    sales_start_at=start,
                )
            self.stdout.write(self.style.SUCCESS(
                "Utworzono wydarzenie demo: /wydarzenia/test-koncert/"
            ))
        else:
            self.stdout.write("Wydarzenie demo już istnieje.")
