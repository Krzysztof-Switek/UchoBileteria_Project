# Runbook operacyjny — Bileteria UCHO

## 1. Wdrożenie na VPS

1. Zainstaluj Dockera i Docker Compose na VPS (Hetzner/OVH, 2 GB RAM wystarczy).
2. Sklonuj repo, skopiuj `.env.example` → `.env` i ustaw:
   - `SECRET_KEY` (długi losowy), `DEBUG=False`,
   - `ALLOWED_HOSTS=bilety.twojadomena.pl`, `CSRF_TRUSTED_ORIGINS=https://bilety.twojadomena.pl`,
   - `SITE_BASE_URL=https://bilety.twojadomena.pl`,
   - `POSTGRES_PASSWORD` (losowe),
   - `EMAIL_URL=smtp://user:pass@smtp.dostawca.pl:587` + `DEFAULT_FROM_EMAIL`.
   - `CLUB_ADDRESS`, `CLUB_PHONE` (i opcjonalnie inny `CLUB_CONTACT_EMAIL`) — bez nich stopka publicznej strony pokazuje tylko nazwę klubu i e-mail. Uzupełnij też realną treść `/regulamin/` i `/polityka-prywatnosci/` (`templates/legal/*.html`) — dziś to szkice z placeholderami (KUP-04), nie gotowa treść prawna.
3. W `Caddyfile` podmień domenę (Caddy sam wystawi HTTPS przez Let's Encrypt).
4. `docker compose up -d --build`
5. Pierwsze uruchomienie:
   ```bash
   docker compose exec app python manage.py migrate
   docker compose exec app python manage.py setup_roles
   docker compose exec app python manage.py createsuperuser
   ```
6. System działa w trybie **DEMO** — można bezpiecznie testować na produkcyjnym serwerze.

## 2. Konta personelu

Konta zakłada się komendą `manage.py create_staff_user <email> <ROLA>`
(hasło wpisywane interaktywnie, nigdy jako argument), np.:

```bash
python manage.py create_staff_user jan.kowalski@klub.pl EVENT_MANAGER
```

- Loginem jest zawsze adres e-mail (logowanie po e-mailu, wielkość liter bez znaczenia).
- Role: `ADMIN`, `EVENT_MANAGER`, `SALES_MANAGER`, `READ_ONLY` → dostają `is_staff`
  i logują się na `/admin/`.
- **`DOOR_STAFF` (bramkarze): bez `is_staff`**, logują się na `/logowanie/`
  i trafiają prosto do `/wejscie/`.
- Hasło musi mieć min. 12 znaków (zaostrzone `AUTH_PASSWORD_VALIDATORS` — panel
  jest publicznie dostępny). Logowanie jest throttlowane per IP+adres e-mail
  (5 nieudanych prób / 15 min, `apps/accounts/ratelimit.py`) — dotyczy zarówno
  `/admin/login/`, jak i `/logowanie/`.
- Wymaga wcześniejszego `manage.py setup_roles` (grupy muszą już istnieć).

## 3. Przejście z DEMO na LIVE (prawdziwe płatności)

1. Załóż konto Stripe, włącz BLIK/karty, ustaw webhook na
   `https://bilety.twojadomena.pl/webhooks/stripe/`
   (zdarzenia: `checkout.session.completed`, `checkout.session.expired`, `charge.refunded`).
2. Najpierw klucze **testowe** (`sk_test_…`, `whsec_…`) w `.env`, restart,
   przeklik pełnego cyklu kartą testową `4242 4242 4242 4242` (tryb pozostaje DEMO,
   Stripe testujemy oddzielnym eventem po tymczasowym przełączeniu na LIVE z kluczami test).
3. Odhacz całą `docs/PRODUCTION_CHECKLIST.md`.
4. Podmień klucze na **live**, restart.
5. Panel admina → *Konfiguracja płatności* → tryb LIVE (system odmówi bez kluczy).
6. Transakcja kontrolna za małą kwotę + zwrot.
7. Dane demo zostają w bazie (flaga `is_demo`) — raporty mają filtr DEMO/LIVE.

## 4. Backup i odtwarzanie

Backup bazy (dodaj do crona hosta, np. co noc o 3:00):
```bash
docker compose exec -T db pg_dump -U ucho ucho | gzip > /backups/ucho-$(date +%F).sql.gz
find /backups -name 'ucho-*.sql.gz' -mtime +30 -delete
```
Kopiuj też katalog `media/` (kody QR): `rsync -a ./media/ /backups/media/`.

Odtworzenie:
```bash
gunzip -c /backups/ucho-DATA.sql.gz | docker compose exec -T db psql -U ucho ucho
```
**Przetestuj odtwarzanie przed pierwszą prawdziwą imprezą.**

## 5. Procedura awaryjna przy wejściu (brak internetu / awaria skanera)

1. Przed każdą imprezą system zapisuje snapshot listy awaryjnej
   (`media/emergency/…csv`, 2 h przed startem). Można też pobrać ręcznie:
   `/wejscie/<id>/lista-awaryjna.csv`. **Wydrukuj listę przed imprezą.**
2. Gdy skaner nie działa: szukaj na wydruku po kodzie biletu (np. `A7K9-42`)
   lub e-mailu kupującego; odhaczaj wejścia długopisem.
3. Po imprezie nanieś wejścia w panelu (wyszukiwarka `/wejscie/<id>/szukaj/`).

## 6. Odwołanie imprezy

Panel admina → Wydarzenia → zaznacz → akcja **„Odwołaj i zwróć wszystkie
opłacone zamówienia"**. Zwroty idą przez providera zamówienia (demo → demowaluta,
Stripe → prawdziwy zwrot po webhooku `charge.refunded`). Kupujący dostają e-maile.

## 7. Monitoring i diagnostyka

- Logi: `docker compose logs -f app cron`.
- Dziennik zdarzeń: panel admina → *Wpisy audytu* (publikacje, płatności, zwroty, skany, eksporty).
- Webhooki: panel admina → *Zdarzenia płatności* (status PROCESSED/DUPLICATE/ERROR);
  status ERROR = pieniądze bez dopasowanego zamówienia — wymaga ręcznej decyzji.
- Kolejka e-maili: panel admina → *E-maile (kolejka)* — status FAILED po 5 próbach.
- Zadania kalendarza: *Zadania kalendarza* — błędy nie blokują niczego.
- Opcjonalnie podłącz Sentry (`pip install sentry-sdk`, init w `settings.py`).

## 8. Testy obciążeniowe (przed pierwszą dużą sprzedażą)

```bash
pip install locust
EVENT_SLUG=test-koncert locust -f loadtests/locustfile.py --host https://staging -u 50 -r 10
```
Kryterium: brak oversellingu (sold_total ≤ capacity), brak błędów 500.
