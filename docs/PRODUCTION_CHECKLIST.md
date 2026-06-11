# Checklista produkcyjna (spec sekcja 26)

System może przejść na tryb LIVE dopiero, gdy wszystko poniżej jest odhaczone.

## Środowisko
- [ ] Środowisko testowe (DEMO na produkcyjnym serwerze) działa.
- [ ] Produkcja oddzielona od testów (osobna baza/serwer lub czyste dane LIVE przez flagę `is_demo`).
- [ ] `DEBUG=False`, `SECRET_KEY` losowy, HTTPS działa (Caddy), `ALLOWED_HOSTS` poprawne.
- [ ] Backup bazy skonfigurowany w cronie **i przetestowane odtwarzanie**.

## Płatności
- [ ] Testy sandbox Stripe przechodzą (karta testowa, pełny cykl).
- [ ] Weryfikacja podpisu webhooka działa (zły podpis → 400) — pokryte testami.
- [ ] Podwójny webhook nie dubluje biletów — pokryte testami.
- [ ] Webhook Stripe skonfigurowany na produkcyjnej domenie.
- [ ] Transakcja kontrolna za małą kwotę + zwrot wykonane na kluczach live.

## Pojemność i bilety
- [ ] Pojemność wydarzenia nie może być przekroczona — pokryte testem współbieżnym.
- [ ] Pojemność puli nie może być przekroczona — pokryte testami.
- [ ] QR działa na telefonie (test z prawdziwym e-mailem i kamerą).
- [ ] Drugi skan tego samego biletu odrzucony — pokryte testami + test ręczny.
- [ ] Bilet zwrócony odrzucany przy wejściu — pokryte testami.
- [ ] Bilet anulowany odrzucany przy wejściu — pokryte testami.

## Operacje
- [ ] Eksport listy awaryjnej działa; snapshot 2 h przed imprezą generuje się.
- [ ] Uprawnienia panelu działają (DOOR_STAFF bez danych finansowych) — pokryte testami.
- [ ] Google Calendar tworzy/aktualizuje wydarzenia (lub świadomie wyłączony).
- [ ] E-maile dochodzą z produkcyjnego SMTP (test na Gmail i innej skrzynce).
- [ ] Audyt zapisuje wpisy (sprawdź w panelu po przekliku).
- [ ] Proces zwrotu przetestowany (pojedynczy + masowy przy odwołaniu).
- [ ] Procedura awaryjnego wejścia opisana i **wydrukowana** (RUNBOOK §5).
- [ ] Obsługa przetestowała check-in na telefonie przy klubowym Wi-Fi/LTE.
- [ ] Test obciążeniowy wykonany (50 równoczesnych zakupów, bez oversellingu).

## Pilotaż (spec fazy 8-9)
- [ ] Wewnętrzna fałszywa impreza w trybie DEMO przeprowadzona od A do Z.
- [ ] Pierwsza prawdziwa impreza: mała, z nadzorem technicznym i wydrukowaną listą.
