# Bileteria UCHO

System sprzedaży biletów dla klubu muzycznego (~500 osób, ~100 imprez rocznie).
Specyfikacja produktu: `Plans_TO_DO_lists/TICKETING_SPEC.md`.

## Najważniejsza zasada: tryb DEMO

System startuje w **trybie demo** — cały cykl (zakup → płatność → bilet z QR →
check-in → zwrot → raporty i przepływy) działa na wirtualnej walucie `DEMO`,
bez dotykania prawdziwych pieniędzy. Webhooki demo przechodzą przez dokładnie
ten sam potok co webhooki Stripe (podpis, idempotencja, `payment_events`).

Przełącznik trybu: panel admina → *Konfiguracja płatności* (singleton).
Przejście na LIVE wymaga skonfigurowanych kluczy Stripe i jest logowane w audycie.
Zamówienia demo są trwale oznaczone `is_demo` i nigdy nie mieszają się z danymi
produkcyjnymi w raportach.

## Szybki start (dev, Windows/Linux)

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
cp .env.example .env          # DEBUG=True wystarczy na start
python manage.py migrate
python manage.py setup_roles  # grupy: ADMIN, EVENT_MANAGER, SALES_MANAGER, DOOR_STAFF, READ_ONLY
python manage.py createsuperuser
python manage.py seed_demo    # przykładowe wydarzenie z 3 pulami
python manage.py runserver
```

Przeklik trybu demo: `/wydarzenia/test-koncert/` → kup bilet → kasa demo →
zapłać → bilet z QR na stronie zamówienia → `/wejscie/` (skaner) →
`/raporty/` (przepływy DEMO). E-maile w dev: `python manage.py send_emails`
(backend konsolowy lub Mailpit przez `EMAIL_URL=smtp://localhost:1025`).

## Testy i lint

```bash
pytest          # ~180 testów, w tym współbieżne (overselling, podwójny skan)
ruff check .
```

## Struktura

| Aplikacja | Odpowiedzialność |
|---|---|
| `apps/events` | wydarzenia, pule cenowe (aktywacja regułą C), publikacja, Google Calendar (outbox) |
| `apps/orders` | zamówienia, atomowa rezerwacja pojemności, providerzy płatności (DEMO/Stripe), webhooki, zwroty |
| `apps/tickets` | wydawanie biletów, QR (hash w DB), kolejka e-maili z retry |
| `apps/checkin` | skaner QR, wyszukiwanie ręczne, lista awaryjna CSV |
| `apps/reports` | sprzedaż i przepływy z filtrem demo/live, eksporty CSV |
| `apps/accounts` | role i uprawnienia |
| `apps/auditlog` | dziennik zdarzeń |

## Zadania cykliczne (cron co minutę — kontener `cron` w compose)

- `expire_orders` — wygasza nieopłacone zamówienia (20 min) i zwalnia miejsca,
- `send_emails` — wysyła kolejkę e-maili (5 prób),
- `sync_calendar` — synchronizuje Google Calendar (nieblokująco),
- `snapshot_emergency_lists` — CSV listy awaryjnej 2 h przed imprezą.

## Produkcja

`compose.yml` (app + Postgres 17 + Caddy + cron) na VPS. Szczegóły wdrożenia,
backupy i procedury awaryjne: `docs/RUNBOOK.md`. Przed startem sprzedaży:
`docs/PRODUCTION_CHECKLIST.md`.
