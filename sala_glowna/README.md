# Sala główna — eksport do planowania (Scena Ucho / Podwórko.art)

Wygenerowano 2026-10-05 15:43 z projektu UCHO TECHNICAL MODEL (Podwórko.art / Scena Ucho) (`scripts/export/hall_layout.py`).
Pliki są wynikiem generowania — nie edytuj ich; zmiany zgłaszaj do modelu źródłowego.

## Pliki
- `sala_glowna.json` — pełny opis (geometria + status danych). Główne źródło do importu.
- `sala_glowna.dxf` — rysunek CAD, jednostka: metr; warstwy: `SALA_OBRYS`, `BUFET_ZA_LADA`, `SCIANY`, `WEJSCIA`, `SLUPY`, `SCENA`, `SCHODY`, `PODESTY`, `WYPOSAZENIE`, `BARIERKI`, `NAD_GLOWA`, `OPISY`.
- `sala_glowna.svg` — podgląd w skali 1:50 (współrzędne rysunku w metrach, oś Y odwrócona do rysowania).

## Układ współrzędnych
- Jednostki: metry. Punkt (0,0): Wewnętrzny narożnik sali przy bufecie — przecięcie lica ściany południowej sali (od strony zaplecza) z licem ściany wschodniej bufetu, na poziomie posadzki sali (±0,00). Punkt przy dolnym końcu schodów na antresolę / za ladą. Wszystkie punkty sali mają X ≥ 0 i Y ≥ 0.
- X: lewo/prawo patrząc z widowni na scenę; +X: w prawo patrząc z widowni na scenę (= na północ, w stronę antresoli / muru płn.)
- Y: od widowni w kierunku tylnej ściany sceny; +Y: w kierunku tylnej ściany sceny (= na zachód)
- Z: wysokość od poziomu podłogi sali; Z=0 = posadzka sali
- Orientacja planu: rysując z +Y do góry: scena u góry, bar na dole, ściana płd. (wejścia) po lewej.
- Zasięg sali: X 0.00…11.30, Y 0.00…22.30.

## Zawartość JSON
- `hall.outline` — obrys sali (wielokąt XY). Część przy barze kończy się na froncie blatu lady.
- `context_rooms` — strefa za ladą (bufet), tylko kontekst.
- `walls`, `entrances` (drzwi/otwory w ścianach sali), `columns` (koła: środek + średnica),
  `stage`, `stairs`, `platforms`, `fixtures` (lada), `barrier` (linia barierek, demontowalne).
- `overhead` — rzuty tego, co jest NAD posadzką sali (cień): antresola, biuro/VIP nad barem,
  stanowisko DJ, oraz spód konstrukcji antresoli z prześwitem `clearance_m`.
- `unknown_geometry` — elementy, o których wiadomo, ale bez znanego położenia/kształtu.
- `data_status` każdego obiektu: `measured` (pomiar 2026), `measured_partly` (część wymiarów zmierzona,
  reszta z rysunku), `drawing_2005` (tylko odczyt z inwentaryzacji 2005 — może się różnić od stanu obecnego).

## Obiekty
| kategoria | ID | nazwa | status |
|---|---|---|---|
| walls | `VEN_WALL_ZEWN_PLN` | Ściana zewnętrzna północna (sala) | measured_partly |
| walls | `VEN_WALL_ZEWN_ZACH` | Ściana zewnętrzna zachodnia (za sceną) | measured_partly |
| walls | `VEN_WALL_MUR_WEWN_PLN` | Mur ceglany wewnętrzny północny (pod antresolą) | measured_partly |
| walls | `VEN_WALL_SALA_PLD` | Ściana południowa sali (od strony zaplecza) | measured_partly |
| entrances | `VEN_DOOR_BACKSTAGE_SALA` | Drzwi backstage → sala (dwuskrzydłowe, 180 cm) | measured_partly |
| entrances | `VEN_OPENING_SALA_GARDEROBA` | Otwór sala → garderoba (240 cm) | drawing_2005 |
| entrances | `VEN_OPENING_SALA_KORYTARZ` | Otwór sala → korytarz 03 (100 cm) | drawing_2005 |
| entrances | `VEN_DOOR_BUFET_ZAPLECZE` | Drzwi bufet → zaplecze baru (100 cm) | drawing_2005 |
| columns | `VEN_COLUMN_ANT_A1` | Słup stalowy Ø150 — oś płn. antresoli nr 1 | measured_partly |
| columns | `VEN_COLUMN_ANT_A2` | Słup stalowy Ø150 — oś płn. antresoli nr 2 | measured_partly |
| columns | `VEN_COLUMN_ANT_A3` | Słup stalowy Ø150 — oś płn. antresoli nr 3 | measured_partly |
| columns | `VEN_COLUMN_ANT_A4` | Słup stalowy Ø150 — oś płn. antresoli nr 4 | measured_partly |
| columns | `VEN_COLUMN_ANT_A5` | Słup stalowy Ø150 — narożnik osi płn./wsch. antresoli | measured_partly |
| columns | `VEN_COLUMN_ANT_B2` | Słup stalowy Ø150 — oś zach. skrzydła wsch. nr 2 | measured_partly |
| columns | `VEN_COLUMN_ANT_B3` | Słup stalowy Ø150 — oś zach. skrzydła wsch. nr 3 | measured_partly |
| columns | `VEN_COLUMN_SCH_N` | Słup stalowy Ø150 — schody — narożnik płn.-wsch. biegu | drawing_2005 |
| columns | `VEN_COLUMN_SCH_S` | Słup stalowy Ø150 — schody — narożnik płd.-wsch. biegu | drawing_2005 |
| columns | `VEN_COLUMN_BAR_1` | Słup stalowy Ø150 — przed ladą bufetu nr 1 | measured_partly |
| columns | `VEN_COLUMN_BAR_2` | Słup stalowy Ø150 — przed ladą bufetu nr 2 | measured_partly |
| columns | `VEN_COLUMN_BAR_3` | Słup stalowy Ø150 — przed ladą bufetu nr 3 | measured_partly |
| columns | `VEN_COLUMN_BAR_4` | Słup stalowy Ø150 — przy ścianie płd. nr 4 | drawing_2005 |
| stage | `VEN_STAGE_MAIN` | 11 Scena +0,75 | measured_partly |
| stairs | `VEN_STAIR_ANTRESOLA_1` | Schody na antresolę — bieg 1 (14 stopni 16/25) | drawing_2005 |
| stairs | `VEN_STAIR_ANTRESOLA_2` | Schody na antresolę — bieg 2 (4 stopnie 16/25) | drawing_2005 |
| platforms | `VEN_PLATFORM_SPOCZNIK` | Spocznik schodów na antresolę (+2,24) | drawing_2005 |
| fixtures | `VEN_BAR_COUNTER` | Lada bufetu (13) | measured_partly |
| barrier | `VEN_BARIERKI_FOSA` | Barierki fosy przed sceną (demontowalne) | measured |
| overhead | `VEN_ROOM_ANTRESOLA` | 22 Antresola | measured_partly |
| overhead | `VEN_ROOM_POM_TECH_23` | 23 Biuro (nad bufetem) | drawing_2005 |
| overhead | `VEN_ROOM_POM_TECH_24` | 24 VIP room (nad bufetem) | drawing_2005 |
| overhead | `VEN_PLATFORM_DJ` | Stanowisko D.J. (rzut antresoli) | drawing_2005 |
| overhead | `VEN_CEILING_POD_ANTRESOLA` | Spód konstrukcji antresoli (prześwit nad posadzką sali) | measured |
| unknown_geometry | `VEN_COLUMN_DJ` | Kolumna na narożniku stanowiska DJ (czarny walec na scenie, płd.-zach.) | measured_partly |
| unknown_geometry | `VEN_STAIR_SCENA` | Schody na scenę (płd.-zach., przy kolumnie DJ) | measured_partly |

## Uwagi
- Wartości `null` = nieznane (UNKNOWN w modelu) — nie zgadywać.
- Obiekty `drawing_2005` traktuj jako orientacyjne; przy kolizji z planem stolików sprawdź na miejscu.
