import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.core.validators import validate_email

from apps.accounts.roles import ROLE_PERMISSIONS


class Command(BaseCommand):
    help = (
        "Create a staff account that logs in with an e-mail address, assigned "
        "to one role group. Run `manage.py setup_roles` first if groups don't "
        "exist yet. Password is entered interactively (never as a CLI arg)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("email", help="Adres e-mail pracownika (będzie loginem).")
        parser.add_argument(
            "role",
            choices=list(ROLE_PERMISSIONS.keys()),
            help="Grupa uprawnień, np. ADMIN, EVENT_MANAGER, SALES_MANAGER, "
            "READ_ONLY, DOOR_STAFF.",
        )

    def handle(self, *args, **options):
        UserModel = get_user_model()
        email = options["email"].strip().lower()
        role = options["role"]

        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        if UserModel.objects.filter(email__iexact=email).exists():
            raise CommandError(f"Konto z adresem {email} już istnieje.")

        try:
            group = Group.objects.get(name=role)
        except Group.DoesNotExist as exc:
            raise CommandError(
                f"Grupa '{role}' nie istnieje — uruchom najpierw `manage.py setup_roles`."
            ) from exc

        password = getpass.getpass("Hasło: ")
        if password != getpass.getpass("Powtórz hasło: "):
            raise CommandError("Hasła się nie zgadzają.")

        try:
            validate_password(password)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc

        # DOOR_STAFF must never get is_staff (they only use /wejscie/, not /admin/).
        is_staff = role != "DOOR_STAFF"
        user = UserModel.objects.create_user(
            username=email,
            email=email,
            password=password,
            is_staff=is_staff,
        )
        user.groups.add(group)

        self.stdout.write(self.style.SUCCESS(f"Utworzono konto {email} w roli {role}."))
