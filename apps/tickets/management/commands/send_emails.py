from django.core.management.base import BaseCommand

from apps.tickets.sending import send_pending_emails


class Command(BaseCommand):
    help = "Send queued e-mails from the outbox (run via cron)."

    def handle(self, *args, **options):
        sent = send_pending_emails()
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} e-mail(s)."))
