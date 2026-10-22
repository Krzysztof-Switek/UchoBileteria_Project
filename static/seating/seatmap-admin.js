/*
 * Staff seat map of one event (templates/admin/seating/event_map.html):
 * who sits where, block/unblock free seats, move a buyer to another seat.
 * Also renders the layout thumbnails on the same page.
 */
(function () {
    "use strict";

    const M = window.UchoSeatMap;

    function getCookie(name) {
        const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
        return m ? decodeURIComponent(m.pop()) : "";
    }

    function esc(text) {
        const d = document.createElement("div");
        d.textContent = text == null ? "" : String(text);
        return d.innerHTML;
    }

    const STATUS = { AVAILABLE: "wolne", HELD: "czeka na płatność", SOLD: "sprzedane", BLOCKED: "zablokowane" };

    function thumbs() {
        const t = document.getElementById("ucho-thumbs");
        const r = document.getElementById("ucho-rooms");
        if (!t || !r) return;
        const data = JSON.parse(t.textContent);
        const rooms = JSON.parse(r.textContent);
        document.querySelectorAll("[data-thumb]").forEach((host) => {
            const item = data[host.dataset.thumb];
            if (item) new M.SeatMap(host, { mode: "preview", room: rooms[item.room], layout: item.layout });
        });
    }

    function staffMap() {
        const cfgEl = document.getElementById("sm-admin-config");
        const host = document.getElementById("sm-admin-map");
        if (!cfgEl || !host) return;
        const cfg = JSON.parse(cfgEl.textContent);
        if (!cfg) return;
        const list = document.getElementById("sma-list");
        const hint = document.getElementById("sma-hint");
        const actions = document.getElementById("sma-actions");
        const message = document.getElementById("sma-message");
        const movePanel = document.getElementById("sma-move");
        let moveFrom = null;
        let moveTo = null;

        const map = new M.SeatMap(host, {
            mode: "admin", room: cfg.room, layout: cfg.layout, states: {},
            onSeatActivate: (info) => {
                if (!info) return;
                const key = info.seat.id;
                if (moveFrom) {
                    if (map.stateOf(key) !== "AVAILABLE") {
                        say("Miejsce docelowe musi być wolne.", true);
                        return;
                    }
                    moveTo = key;
                    map.setHighlight([moveFrom, key]);
                    document.getElementById("sma-move-to").innerHTML = "Na: <b>" + esc(info.label) + "</b>";
                    movePanel.querySelector('[data-act="move-confirm"]').disabled = false;
                    return;
                }
                const next = new Set(map.selected);
                if (next.has(key)) next.delete(key);
                else next.add(key);
                map.setSelected([...next]);
                renderPanel();
            },
            onTableActivate: (objId) => {
                if (moveFrom) return;
                const keys = map.groupSeats(objId).map((s) => s.seat.id);
                const next = new Set(map.selected);
                const all = keys.every((k) => next.has(k));
                keys.forEach((k) => (all ? next.delete(k) : next.add(k)));
                map.setSelected([...next]);
                renderPanel();
            },
        });

        function say(text, isError) {
            message.textContent = text || "";
            message.className = "ucho-eventmap-message" + (isError ? " is-error" : text ? " is-ok" : "");
        }

        function renderPanel() {
            const seats = map.selectedSeats();
            hint.hidden = seats.length > 0;
            list.innerHTML = seats.map((s) => {
                const st = map.states[s.seat.id] || {};
                let html = "<li><b>" + esc(s.label) + "</b> — " + esc(STATUS[st.status] || st.status || "?");
                if (st.zone) html += " · strefa " + esc(st.zone);
                if (st.order) {
                    html += '<br><a href="' + esc(st.order_url) + '">zamówienie ' + esc(st.order) + "</a> · " + esc(st.email) +
                        " · " + esc(st.order_status);
                }
                if (st.ticket) html += "<br>bilet " + esc(st.ticket) + (st.checked_in ? " · <b>już wszedł</b>" : "");
                if (st.refund_url) html += '<br><a href="' + esc(st.refund_url) + '">Zwróć ten bilet…</a>';
                if (st.note) html += "<br>notatka: " + esc(st.note);
                return html + "</li>";
            }).join("");
            if (!actions) return;
            actions.hidden = !seats.length || !!moveFrom;
            const statuses = seats.map((s) => (map.states[s.seat.id] || {}).status);
            document.getElementById("sma-block").hidden = !statuses.includes("AVAILABLE");
            actions.querySelector('[data-act="unblock"]').hidden = !statuses.includes("BLOCKED");
            actions.querySelector('[data-act="move"]').hidden = !(seats.length === 1 && (statuses[0] === "SOLD" || statuses[0] === "HELD"));
        }

        async function load() {
            const response = await fetch(cfg.urls.state, { credentials: "same-origin" });
            if (!response.ok) return;
            const data = await response.json();
            map.setStates(data.states);
            const counts = { SOLD: 0, HELD: 0, AVAILABLE: 0, BLOCKED: 0, capacity: data.capacity };
            Object.values(data.states).forEach((s) => {
                counts[s.status] = (counts[s.status] || 0) + 1;
            });
            document.querySelectorAll("[data-stat]").forEach((node) => {
                node.textContent = counts[node.dataset.stat];
            });
            renderPanel();
        }

        async function act(body) {
            const response = await fetch(cfg.urls.action, {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
                body: JSON.stringify(body),
            });
            let data = {};
            try {
                data = await response.json();
            } catch (e) {
                data = { ok: false, message: "Błąd serwera (" + response.status + ")." };
            }
            say(data.message, !data.ok);
            await load();
            return data.ok;
        }

        if (actions) {
            actions.addEventListener("click", async (e) => {
                const b = e.target.closest("[data-act]");
                if (!b) return;
                const keys = map.selectedSeats().map((s) => s.seat.id);
                if (b.dataset.act === "block") {
                    const ok = await act({ action: "block", seats: keys, note: document.getElementById("sma-note").value });
                    if (ok) map.setSelected([]);
                } else if (b.dataset.act === "unblock") {
                    const ok = await act({ action: "unblock", seats: keys });
                    if (ok) map.setSelected([]);
                } else if (b.dataset.act === "clear") {
                    map.setSelected([]);
                } else if (b.dataset.act === "move") {
                    moveFrom = keys[0];
                    moveTo = null;
                    map.setSelected([]);
                    map.setHighlight([moveFrom]);
                    document.getElementById("sma-move-from").textContent = map.seatNodes.get(moveFrom).label;
                    document.getElementById("sma-move-to").textContent = "";
                    movePanel.querySelector('[data-act="move-confirm"]').disabled = true;
                    movePanel.hidden = false;
                }
                renderPanel();
            });
            movePanel.addEventListener("click", async (e) => {
                const b = e.target.closest("[data-act]");
                if (!b) return;
                if (b.dataset.act === "move-confirm" && moveFrom && moveTo) {
                    await act({ action: "move", from: moveFrom, to: moveTo, notify: document.getElementById("sma-notify").checked });
                }
                moveFrom = null;
                moveTo = null;
                map.setHighlight([]);
                movePanel.hidden = true;
                renderPanel();
            });
        }

        load();
        setInterval(() => {
            if (!document.hidden && !moveFrom) load();
        }, 15000);
    }

    document.addEventListener("DOMContentLoaded", () => {
        thumbs();
        staffMap();
    });
})();
