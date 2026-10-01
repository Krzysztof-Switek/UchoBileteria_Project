from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.accounts import dev_credentials


class Command(BaseCommand):
    help = (
        "Create/refresh local demo accounts from dev_credentials.json (generated with "
        "random passwords on first run). Only with DEBUG=True and payments in DEMO mode."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--regenerate",
            action="store_true",
            help="Wygeneruj plik od nowa (nowe hasła) zamiast używać istniejącego.",
        )

    def handle(self, *args, **options):
        if not dev_credentials.enabled():
            raise CommandError(
                "Konta demo działają tylko przy DEBUG=True i trybie płatności DEMO."
            )

        path = dev_credentials.credentials_path()
        accounts = [] if options["regenerate"] else dev_credentials.read_accounts()
        stale = dev_credentials.unknown_roles(accounts)
        if stale:
            raise CommandError(
                f"Plik zawiera nieistniejące role: {', '.join(sorted(stale))}. "
                "Popraw je w pliku albo uruchom z --regenerate."
            )
        if not accounts:
            accounts = dev_credentials.default_accounts()
            dev_credentials.write_accounts(accounts)
            self.stdout.write(f"Zapisano nowy plik: {path}")

        for account, created in dev_credentials.sync_accounts(accounts):
            login = account["username"]
            action = "utworzono" if created else "zaktualizowano"
            self.stdout.write(
                f"  {login:<32} {account['password']:<20} "
                f"{account.get('label') or account.get('role') or ''} ({action})"
            )
        self.stdout.write(self.style.SUCCESS(
            f"Gotowe — {len(accounts)} kont. Na stronach logowania są przyciski "
            "szybkiego logowania."
        ))
