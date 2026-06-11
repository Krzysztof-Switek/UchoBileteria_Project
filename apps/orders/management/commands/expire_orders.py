from django.core.management.base import BaseCommand

from apps.orders.services import expire_stale_orders


class Command(BaseCommand):
    help = "Expire unpaid orders past their deadline and release reserved capacity. Run via cron."

    def handle(self, *args, **options):
        count = expire_stale_orders()
        self.stdout.write(self.style.SUCCESS(f"Expired {count} stale order(s)."))
