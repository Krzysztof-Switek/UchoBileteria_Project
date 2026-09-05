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
npm install && npm run build:css   # kompiluje static/dist/tailwind.css lokalnie (bez CDN)
cp .env.example .env          # DEBUG=True wystarczy na start
python manage.py migrate
python manage.py setup_roles  # grupy: ADMIN, EVENT_MANAGER, SALES_MANAGER, DOOR_STAFF, READ_ONLY
python manage.py createsuperuser
python manage.py seed_demo    # przykładowe wydarzenie z 3 pulami
python manage.py runserver
```

Style (`static/src/input.css`) trzeba przebudować po każdej zmianie klas w
szablonach: `npm run build:css` (jednorazowo) albo `npm run watch:css`
(przy aktywnej pracy nad frontendem). Obraz Dockera robi to automatycznie
w osobnym etapie builda — na produkcji Node nie jest potrzebny w runtime.

Przeklik trybu demo: `/wydarzenia/test-koncert/` → kup bilet → kasa demo →
zapłać → bilet z QR na stronie zamówienia → `/wejscie/` (skaner) →
`/raporty/` (przepływy DEMO).

E-maile wychodzą **natychmiast po opłaceniu** (kolejka + cron `send_emails`
to tylko ponawianie po awarii SMTP). W dev bez `EMAIL_URL` lądują w konsoli
serwera; ustaw `EMAIL_URL=smtp://...` w `.env`, aby dostawać prawdziwe
wiadomości (np. Mailpit na `smtp://localhost:1025` albo realny SMTP).

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
