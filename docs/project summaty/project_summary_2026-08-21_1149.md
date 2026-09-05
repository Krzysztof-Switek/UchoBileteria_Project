# Spis treści projektu — Bileteria UCHO

_Wygenerowano automatycznie przez `project_summary.py` — 2026-08-21 11:49 (branch: `master`)._

> Ten plik to punkt wejścia na start sesji: daje obraz stanu repo,
> dokumentów i kodu bez czytania wszystkiego od zera. Szczegóły są
> w plikach źródłowych wskazanych niżej.

---

## 1. Status Gita

**Branch:** `master`

**Niezacommitowane zmiany (3):**
- `M .idea/misc.xml`
- `?? "docs/Hendouts TODOs/"`
- `?? project_summary.py`

**Ostatnie 15 commitów:**
- `befa0c3 2026-06-11 Send e-mails immediately after payment commit (outbox stays as retry net)`
- `4c9262b 2026-06-11 Phase 12: E2E lifecycle test, load-test harness, ops docs, cron container`
- `ded33c4 2026-06-11 Phase 11: Stripe provider behind the shared PaymentProvider interface`
- `797aa5c 2026-06-11 Phase 10: Google Calendar sync via non-blocking outbox`
- `bfa3718 2026-06-11 Phase 9: role groups and permission gates`
- `a23ebc9 2026-06-11 Phase 8: refunds (single + event-wide) and sales/cash-flow reports`
- `0f5125d 2026-06-11 Phase 7: check-in scanner, manual lookup, emergency CSV list`
- `d8ae44a 2026-06-11 Phase 6: ticket issuing, QR codes, e-mail outbox with retries`
- `11adef6 2026-06-11 Phase 5: demo payment provider with full webhook pipeline`
- `e9137d5 2026-06-11 Phase 4: orders with atomic capacity reservation, expiry, rate limiting`
- `39e9d5e 2026-06-11 Phase 3: public event list and detail pages with demo banner`
- `6f7543a 2026-06-11 Phase 2: pool activation logic, sales state, publish/cancel services`
- `9889d61 2026-06-11 Phase 1: data model (events, pools, orders, tickets, payment events, audit log)`
- `69ec123 2026-06-11 Phase 0: project scaffold (Django 6, env settings, Docker, CI, smoke tests)`

## 2. Kluczowe dokumenty

- **Bileteria UCHO** (`README.md`)
  System sprzedaży biletów dla klubu muzycznego (~500 osób, ~100 imprez rocznie). Specyfikacja produktu: `Plans_TO_DO_lists/TICKETING_SPEC.md`.
- **Runbook operacyjny — Bileteria UCHO** (`docs/RUNBOOK.md`)
  1. Zainstaluj Dockera i Docker Compose na VPS (Hetzner/OVH, 2 GB RAM wystarczy). 2. Sklonuj repo, skopiuj `.env.example` → `.env` i ustaw: - `SECRET_KEY` (długi losowy), `DEBUG=False`, - `ALLOWED_HOSTS=bilety.twojadomena.pl`,…
- **Checklista produkcyjna (spec sekcja 26)** (`docs/PRODUCTION_CHECKLIST.md`)
  System może przejść na tryb LIVE dopiero, gdy wszystko poniżej jest odhaczone.

## 3. Handouty, plany i TODO (`docs/Hendouts TODOs`)

### Podsumowania sesji
- **Podsumowanie sesji — 11.06.2026** (`docs/Hendouts TODOs/11.06_session_summary.md`, 2026-06-11)
  Przemyślenie specyfikacji `TICKETING_SPEC.md` (bileteria dla klubu na 500 osób, ~100 imprez/rok), przygotowanie kompleksowego planu wdrożenia i jego realizacja faza po fazie wraz z testami. Kluczowe wymaganie: **tryb demo** — pełny cykl płatności na…

### Specyfikacje
- **Ticketing System Specification** (`docs/Hendouts TODOs/TICKETING_SPEC.md`, 2026-06-11)
  **Project name:** Ticketing system for a music club **Document:** `TICKETING_SPEC.md` **Version:** MVP draft v1 **Status:** working specification

## 4. Aplikacje Django (`apps/`)

### `apps/accounts`
- pliki: apps.py, roles.py
- management commands: setup_roles

### `apps/auditlog`
- pliki: admin.py, apps.py, models.py, services.py
- modele: AuditLog

### `apps/checkin`
- pliki: apps.py, services.py, urls.py, views.py
- management commands: snapshot_emergency_lists

### `apps/events`
- pliki: admin.py, apps.py, calendar.py, models.py, services.py, urls.py, views.py
- modele: EventStatus, Event, CalendarAction, CalendarOutbox, PoolManualStatus, TicketPool
- management commands: seed_demo, sync_calendar

### `apps/orders`
- pliki: admin.py, apps.py, context_processors.py, forms.py, models.py, payments.py, ratelimit.py, refunds.py, services.py, urls.py, views.py
- modele: PaymentProviderKind, OrderStatus, Order, PaymentEventStatus, PaymentEvent, PaymentMode, PaymentConfig
- management commands: expire_orders

### `apps/reports`
- pliki: apps.py, services.py, urls.py, views.py

### `apps/tickets`
- pliki: admin.py, apps.py, models.py, sending.py, services.py
- modele: TicketStatus, Ticket, EmailStatus, EmailOutbox
- management commands: send_emails

## 5. Testy (`tests/`)

- `tests/test_calendar.py`
- `tests/test_checkin.py`
- `tests/test_demo_payments.py`
- `tests/test_e2e_flow.py`
- `tests/test_models.py`
- `tests/test_orders.py`
- `tests/test_pool_logic.py`
- `tests/test_public_pages.py`
- `tests/test_refunds_reports.py`
- `tests/test_roles.py`
- `tests/test_smoke.py`
- `tests/test_stripe.py`
- `tests/test_tickets.py`

## 6. Pliki konfiguracyjne i infrastruktura

- `pyproject.toml` — konfiguracja narzędzi (pytest, ruff) (✓)
- `requirements.txt` — zależności produkcyjne (✓)
- `requirements-dev.txt` — zależności deweloperskie (✓)
- `compose.yml` — Docker Compose: app + Postgres + Caddy + cron (✓)
- `Dockerfile` — obraz aplikacji (✓)
- `Caddyfile` — reverse proxy / TLS (✓)
- `.env.example` — szablon zmiennych środowiskowych (✓)

## 7. TODO / FIXME w kodzie

_Brak wpisów TODO/FIXME w kodzie._
