/*
 * Buyer's seat map: event page (choose seats and buy) and the "change seats"
 * page of an unpaid order. Page contract:
 *   json_script #sm-buy-config — {room, layout, price, currency, states_url,
 *       preselected?, max_single?, max_total?, mine?, limit_count?}
 *   #sm-buy-map, #sm-buy-list, #sm-buy-total, #sm-buy-msg, #sm-buy-seats (hidden
 *   input), #sm-buy-submit, #sm-buy-alt (accessible list)
 */
(function () {
    "use strict";

    const M = window.UchoSeatMap;

    function esc(text) {
        const d = document.createElement("div");
        d.textContent = text == null ? "" : String(text);
        return d.innerHTML;
    }

    document.addEventListener("DOMContentLoaded", () => {
        const cfgEl = document.getElementById("sm-buy-config");
        if (!cfgEl) return;
        const cfg = JSON.parse(cfgEl.textContent);
        const listEl = document.getElementById("sm-buy-list");
        const totalEl = document.getElementById("sm-buy-total");
        const msgEl = document.getElementById("sm-buy-msg");
        const seatsInput = document.getElementById("sm-buy-seats");
        const submit = document.getElementById("sm-buy-submit");
        const altHost = document.getElementById("sm-buy-alt");
        const changeMode = !!cfg.limit_count;
        const price = cfg.price ? parseFloat(cfg.price) : null;

        function say(text, kind) {
            msgEl.textContent = text || "";
            msgEl.className = "text-sm mt-3 " + (kind === "error" ? "text-danger" : kind === "warn" ? "text-warning" : "text-muted");
        }

        const zoneOf = (info) => (info.obj.category && (cfg.zone_prices || {})[info.obj.category] ? info.obj.category : "");

        function problems(seats) {
            if (changeMode) {
                if (seats.length !== cfg.limit_count) return "Wybierz dokładnie " + cfg.limit_count + " miejsc(a) — tyle, ile jest w zamówieniu.";
                const before = (cfg.mine || []).map((k) => map.seatNodes.get(k)).filter(Boolean).map(zoneOf).sort().join(",");
                const after = seats.map(zoneOf).sort().join(",");
                if (before !== after) return "Nowe miejsca muszą być w tych samych strefach cenowych co obecne (cena się nie zmienia).";
                return "";
            }
            if (cfg.max_total && seats.length > cfg.max_total) return "W jednym zamówieniu najwyżej " + cfg.max_total + " miejsc.";
            // single seats count against the limit; whole TABLE/BOTH tables don't
            const byGroup = new Map();
            seats.forEach((s) => byGroup.set(s.obj.id, (byGroup.get(s.obj.id) || 0) + 1));
            let single = 0;
            byGroup.forEach((n, objId) => {
                const groupSize = map.groupSeats(objId).filter((s) => map.stateOf(s.seat.id) !== "blocked").length;
                const obj = map.groupSeats(objId)[0].obj;
                const whole = n >= groupSize && (obj.sell_mode === "TABLE" || obj.sell_mode === "BOTH");
                if (!whole) single += n;
            });
            if (cfg.max_single && single > cfg.max_single) {
                return "Pojedynczych miejsc można kupić najwyżej " + cfg.max_single + " (cały stolik nie wlicza się do limitu).";
            }
            return "";
        }

        function update(seats) {
            seats = seats || map.selectedSeats();
            listEl.innerHTML = seats.length
                ? seats.map((s) => {
                    const price = map.seatPrice(s);
                    const zone = zoneOf(s);
                    return '<li class="flex justify-between gap-3"><span>' + esc(s.label) +
                        (zone ? ' <span class="text-muted text-xs">· strefa ' + esc(zone) + "</span>" : "") + "</span>" +
                        '<span class="whitespace-nowrap">' + (price !== null ? esc(M.fmtMoney(price, cfg.currency)) + " " : "") +
                        '<button type="button" class="link-quiet text-xs" data-unselect="' + esc(s.seat.id) + '" aria-label="Usuń ' + esc(s.label) + '">usuń</button></span></li>';
                }).join("")
                : '<li class="text-muted">Kliknij wolne ' + ((cfg.layout.zones || []).length ? "(pusty krążek; kolor obrysu to strefa cenowa)" : "(zielone)") +
                  " miejsca na planie" + (changeMode ? "." : " — albo numer stolika, by wybrać cały stolik.") + "</li>";
            if (price !== null && totalEl) {
                const sum = seats.reduce((acc, s) => acc + (map.seatPrice(s) || 0), 0);
                totalEl.innerHTML = seats.length
                    ? (changeMode ? "Wartość miejsc: " : "Razem " + seats.length + " miejsc(a): ") + "<b>" + esc(M.fmtMoney(sum, cfg.currency)) + "</b>"
                    : "";
            }
            const problem = seats.length ? problems(seats) : "";
            seatsInput.value = seats.map((s) => s.seat.id).join(",");
            submit.disabled = !seats.length || !!problem;
            if (problem) say(problem, "warn");
            else if (msgEl.dataset.sticky !== "1") say("");
            syncAlt();
        }

        const map = new M.SeatMap(document.getElementById("sm-buy-map"), {
            mode: "sell", room: cfg.room, layout: cfg.layout, states: {},
            price: cfg.price, currency: cfg.currency, zonePrices: cfg.zone_prices || {},
            onSelectionChange: (seats) => {
                msgEl.dataset.sticky = "";
                update(seats);
            },
            onMessage: (m) => say(m, "warn"),
            onLost: (lost) => {
                msgEl.dataset.sticky = "1";
                say("Ktoś właśnie kupił: " + lost.map((s) => s.label).join(", ") + " — usunęliśmy je z Twojego wyboru.", "error");
            },
        });

        listEl.addEventListener("click", (e) => {
            const b = e.target.closest("[data-unselect]");
            if (!b) return;
            const next = new Set(map.selected);
            next.delete(b.dataset.unselect);
            map.setSelected([...next]);
            update();
        });

        // Accessible alternative: every seat as a toggle button, grouped by table/row.
        function buildAlt() {
            if (!altHost) return;
            const groups = new Map();
            map.seatNodes.forEach((info) => {
                if (!groups.has(info.obj.id)) groups.set(info.obj.id, []);
                groups.get(info.obj.id).push(info);
            });
            altHost.innerHTML = [...groups.values()].map((seats) =>
                '<div class="mt-2"><div class="text-sm font-semibold">' + esc(M.groupName(seats[0].obj)) + "</div>" +
                '<div class="flex flex-wrap gap-1 mt-1">' + seats.map((s) =>
                    '<button type="button" class="input-theme rounded px-2 py-0.5 text-xs" data-alt="' + esc(s.seat.id) + '">' + esc(s.seat.label) + "</button>").join("") +
                "</div></div>").join("");
            altHost.addEventListener("click", (e) => {
                const b = e.target.closest("[data-alt]");
                if (!b || b.disabled) return;
                const info = map.seatNodes.get(b.dataset.alt);
                map._sellToggle(info.obj.id, info.seat.id);
            });
        }

        function syncAlt() {
            if (!altHost) return;
            altHost.querySelectorAll("[data-alt]").forEach((b) => {
                const key = b.dataset.alt;
                const state = map.stateOf(key);
                const selected = map.selected.has(key);
                b.disabled = !(state === "free" || state === "mine");
                b.setAttribute("aria-pressed", selected ? "true" : "false");
                b.classList.toggle("btn-primary", selected);
                b.title = map.seatNodes.get(key).label + " — " + (selected ? "wybrane" : b.disabled ? "zajęte" : "wolne");
            });
        }

        let first = true;
        async function refresh() {
            try {
                const response = await fetch(cfg.states_url, { credentials: "same-origin", cache: "no-store" });
                if (!response.ok) return;
                const data = await response.json();
                map.setStates(data.states);
                if (first) {
                    first = false;
                    const initial = (cfg.mine && cfg.mine.length ? cfg.mine : cfg.preselected || [])
                        .filter((k) => data.states[k] === "free" || data.states[k] === "mine");
                    if (initial.length) map.setSelected(initial);
                    update();
                }
                if (data.sales_state && data.sales_state !== "ON_SALE" && !changeMode) {
                    msgEl.dataset.sticky = "1";
                    say("Sprzedaż na to wydarzenie właśnie się zakończyła lub miejsca się wyczerpały.", "error");
                    submit.disabled = true;
                }
                syncAlt();
            } catch (e) { /* offline for a moment — next poll will catch up */ }
        }

        // legend: one entry per price zone, in the zone's colour, with its price
        const legend = document.getElementById("sm-buy-legend");
        const zones = (cfg.layout.zones || []).filter((z) => (cfg.zone_prices || {})[z.code]);
        if (legend && zones.length) {
            // availability is the fill, the zone is the ring colour
            legend.querySelector(".sm-legend-free").innerHTML = '<i class="lg-ring"></i>wolne (pusty krążek)';
            const html = zones.map((z, i) => {
                const zp = cfg.zone_prices[z.code];
                return '<span class="sm-z' + i + '"><i class="lg-zone"></i><b>Strefa ' + esc(z.code) + "</b>" +
                    (z.name ? " " + esc(z.name) : "") + " — " + esc(M.fmtMoney(parseFloat(zp.price), cfg.currency)) + "</span>";
            }).join("");
            legend.insertAdjacentHTML("afterbegin", html);
        }

        buildAlt();
        update([]);
        refresh();
        setInterval(() => {
            if (!document.hidden) refresh();
        }, 20000);
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden) refresh();
        });

        const form = submit.closest("form");
        form.addEventListener("submit", (e) => {
            const problem = problems(map.selectedSeats());
            if (!map.selected.size || problem) {
                e.preventDefault();
                say(problem || "Wybierz miejsca na planie.", "error");
            } else {
                submit.disabled = true;
                submit.textContent = "Rezerwuję miejsca…";
            }
        });
    });
})();
