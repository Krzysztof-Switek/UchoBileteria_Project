from django.core.management.base import BaseCommand

from apps.accounts.roles import setup_roles


class Command(BaseCommand):
    help = "Create or refresh the role groups (ADMIN, MANAGER, DOOR_STAFF). Idempotent."

    def handle(self, *args, **options):
        roles = setup_roles()
        self.stdout.write(self.style.SUCCESS(f"Roles ready: {', '.join(roles)}"))
