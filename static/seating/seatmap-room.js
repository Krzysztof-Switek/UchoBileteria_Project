/*
 * "Ustawienia sali" (templates/admin/seating/room.html): the owner clicks
 * elements of the hall to hide/show them and draws own elements (rectangle:
 * two clicks; shape: click points, finish with "Zakończ kształt" / Enter).
 * Saved as Room.overrides — see apps/seating/room.py.
 */
(function () {
    "use strict";

    const M = window.UchoSeatMap;
    const KINDS = [
        ["area", "Strefa z opisem"],
        ["overhead", "Nad głową (przerywana)"],
        ["obstacle", "Przeszkoda na podłodze"],
    ];

    function esc(text) {
        const d = document.createElement("div");
        d.textContent = text == null ? "" : String(text);
        return d.innerHTML;
    }

    function getCookie(name) {
        const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
        return m ? decodeURIComponent(m.pop()) : "";
    }

    function clone(x) {
        return JSON.parse(JSON.stringify(x));
    }

    document.addEventListener("DOMContentLoaded", () => {
        const cfgEl = document.getElementById("sm-room-config");
        if (!cfgEl) return;
        const cfg = JSON.parse(cfgEl.textContent);
        const base = cfg.base;
        const hidden = new Set((cfg.overrides && cfg.overrides.hidden) || []);
        let custom = clone((cfg.overrides && cfg.overrides.custom) || []);
        let picked = null;
        let drawing = null; // {mode: "rect" | "poly", points: []}
        let dirty = false;
        // the page opens locked: walls rarely move, edits need a deliberate unlock
        let locked = true;
        const page = document.querySelector(".ucho-room-page");
        const unlockConfirm = document.getElementById("smr-unlock-confirm");
        const unlockBtn = document.getElementById("smr-unlock");
        if (unlockConfirm && unlockBtn && cfg.can_edit) {
            unlockConfirm.addEventListener("change", () => {
                unlockBtn.disabled = !unlockConfirm.checked;
            });
            unlockBtn.addEventListener("click", () => {
                locked = false;
                page.classList.add("is-unlocked");
                document.getElementById("smr-unlock-box").hidden = true;
            });
        }

        const status = document.getElementById("smr-status") || document.createElement("p");
        const hint = document.getElementById("smr-hint") || document.createElement("p");
        const names = {};
        ["obstacles", "entrances", "context"].forEach((k) => (base[k] || []).forEach((e) => {
            names[e.id] = e.name;
        }));

        function buildRoom() {
            const room = clone(base);
            custom.forEach((c) => {
                const element = { id: c.id, name: c.name, shape: "polygon", points: c.points, custom: true };
                if (c.kind === "obstacle") room.obstacles.push(Object.assign(element, { kind: "custom", blocking: true }));
                else room.context.push(Object.assign(element, { kind: c.kind }));
            });
            return room;
        }

        const map = new M.SeatMap(document.getElementById("sm-room-map"), {
            mode: "room", room: buildRoom(), layout: { objects: [] }, hiddenIds: hidden,
        });

        function setDirty() {
            dirty = true;
            status.textContent = "Niezapisane zmiany";
            status.className = "ucho-editor-status is-dirty";
        }

        function redraw() {
            map.room = buildRoom();
            map.opts.hiddenIds = hidden;
            map.render();
            map.roomLayer.querySelectorAll("[data-room-el]").forEach((node) => {
                node.classList.toggle("is-picked", node.dataset.roomEl === picked);
            });
            drawDraft();
            renderPanel();
        }

        function drawDraft() {
            map.overlay.textContent = "";
            if (!drawing || !drawing.points.length) return;
            const pts = drawing.mode === "rect" && drawing.cursor
                ? rectPoints(drawing.points[0], drawing.cursor)
                : drawing.points.concat(drawing.cursor ? [drawing.cursor] : []);
            if (pts.length >= 2) M.el("polygon", { points: M.pointsAttr(pts), class: "sm-draft" }, map.overlay);
            drawing.points.forEach((p) => M.el("circle", { cx: p[0], cy: p[1], r: 10, class: "sm-draft-point" }, map.overlay));
        }

        function rectPoints(a, b) {
            return [[a[0], a[1]], [b[0], a[1]], [b[0], b[1]], [a[0], b[1]]];
        }

        function snap(p) {
            return [Math.round(p.x / 10) * 10, Math.round(p.y / 10) * 10];
        }

        function nextId() {
            let n = 0;
            custom.forEach((c) => {
                const m = /^c(\d+)$/.exec(c.id);
                if (m) n = Math.max(n, +m[1]);
            });
            return "c" + (n + 1);
        }

        function finishShape(points) {
            if (points.length < 3) {
                hint.textContent = "Kształt musi mieć co najmniej 3 punkty.";
                return;
            }
            const item = { id: nextId(), name: "Nowy element", kind: "area", points };
            custom.push(item);
            picked = item.id;
            drawing = null;
            setMode(null);
            setDirty();
            redraw();
            const input = document.querySelector('[data-custom-name="' + item.id + '"]');
            if (input) {
                input.focus();
                input.select();
            }
        }

        function setMode(mode) {
            document.querySelectorAll("[data-draw]").forEach((b) => b.classList.toggle("is-active", b.dataset.draw === mode));
            const finish = document.getElementById("smr-finish");
            const cancel = document.getElementById("smr-cancel");
            if (finish) finish.hidden = mode !== "poly";
            if (cancel) cancel.hidden = !mode;
            if (mode === "rect") hint.textContent = "Kliknij pierwszy róg prostokąta, potem przeciwległy róg.";
            else if (mode === "poly") hint.textContent = "Klikaj kolejne punkty kształtu; zakończ przyciskiem „Zakończ kształt” albo klawiszem Enter.";
            else hint.textContent = "Kliknij element planu, żeby go ukryć albo przywrócić. Własne elementy rysujesz przyciskami powyżej.";
        }

        // clicks on the plan (a drag is a pan, not a click)
        let downAt = null;
        map.svg.addEventListener("pointerdown", (e) => {
            downAt = { x: e.clientX, y: e.clientY };
        });
        map.svg.addEventListener("click", (e) => {
            if (downAt && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 5) return;
            if (locked) return;
            if (drawing) {
                const p = snap(map.clientToWorld(e.clientX, e.clientY));
                drawing.points.push(p);
                if (drawing.mode === "rect" && drawing.points.length === 2) {
                    finishShape(rectPoints(drawing.points[0], drawing.points[1]));
                    return;
                }
                drawDraft();
                return;
            }
            const node = e.target.closest("[data-room-el]");
            if (!node) {
                picked = null;
                redraw();
                return;
            }
            const id = node.dataset.roomEl;
            if (custom.some((c) => c.id === id)) {
                picked = picked === id ? null : id;
            } else {
                if (hidden.has(id)) hidden.delete(id);
                else hidden.add(id);
                setDirty();
            }
            redraw();
        });
        map.svg.addEventListener("pointermove", (e) => {
            if (!drawing || !drawing.points.length) return;
            drawing.cursor = snap(map.clientToWorld(e.clientX, e.clientY));
            drawDraft();
        });
        document.addEventListener("keydown", (e) => {
            if (!drawing) return;
            if (e.key === "Enter" && drawing.mode === "poly") {
                e.preventDefault();
                finishShape(drawing.points);
            } else if (e.key === "Escape") {
                drawing = null;
                setMode(null);
                drawDraft();
            }
        });

        document.querySelectorAll("[data-draw]").forEach((b) => b.addEventListener("click", () => {
            drawing = { mode: b.dataset.draw, points: [] };
            setMode(b.dataset.draw);
            drawDraft();
        }));
        if (!cfg.can_edit) {
            renderPanel();
            return; // view only: no drawing, no saving
        }
        document.getElementById("smr-finish").addEventListener("click", () => drawing && finishShape(drawing.points));
        document.getElementById("smr-cancel").addEventListener("click", () => {
            drawing = null;
            setMode(null);
            drawDraft();
        });

        function renderPanel() {
            const hiddenList = document.getElementById("smr-hidden");
            hiddenList.innerHTML = hidden.size
                ? [...hidden].map((id) => "<li><span>" + esc(names[id] || id) + '</span><button type="button" class="button" data-unhide="' + esc(id) + '">Przywróć</button></li>').join("")
                : '<li class="ucho-muted">Nic nie jest ukryte.</li>';
            const customList = document.getElementById("smr-custom");
            customList.innerHTML = custom.length
                ? custom.map((c) => '<li class="' + (c.id === picked ? "is-picked" : "") + '">' +
                    '<input type="text" maxlength="60" value="' + esc(c.name) + '" data-custom-name="' + esc(c.id) + '" aria-label="Nazwa">' +
                    '<select data-custom-kind="' + esc(c.id) + '" aria-label="Rodzaj">' +
                    KINDS.map(([v, l]) => '<option value="' + v + '"' + (c.kind === v ? " selected" : "") + ">" + l + "</option>").join("") +
                    '</select><button type="button" class="button" data-custom-del="' + esc(c.id) + '" title="Usuń">✕</button></li>').join("")
                : '<li class="ucho-muted">Brak — narysuj prostokąt albo kształt.</li>';
        }

        document.getElementById("smr-hidden").addEventListener("click", (e) => {
            const b = e.target.closest("[data-unhide]");
            if (!b) return;
            hidden.delete(b.dataset.unhide);
            setDirty();
            redraw();
        });
        const customList = document.getElementById("smr-custom");
        customList.addEventListener("change", (e) => {
            const nameId = e.target.dataset.customName;
            const kindId = e.target.dataset.customKind;
            const item = custom.find((c) => c.id === (nameId || kindId));
            if (!item) return;
            if (nameId) item.name = e.target.value.trim() || item.name;
            if (kindId) item.kind = e.target.value;
            setDirty();
            redraw();
        });
        customList.addEventListener("click", (e) => {
            const b = e.target.closest("[data-custom-del]");
            if (!b) return;
            custom = custom.filter((c) => c.id !== b.dataset.customDel);
            if (picked === b.dataset.customDel) picked = null;
            setDirty();
            redraw();
        });

        async function post(body) {
            status.textContent = "Zapisywanie…";
            status.className = "ucho-editor-status";
            const response = await fetch(cfg.urls.save, {
                method: "POST", credentials: "same-origin",
                headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
                body: JSON.stringify(Object.assign(body, {
                    refresh: document.getElementById("smr-refresh").checked,
                })),
            });
            let data = {};
            try {
                data = await response.json();
            } catch (err) {
                data = { ok: false, message: "Błąd serwera (" + response.status + ")." };
            }
            status.textContent = data.message || (data.ok ? "Zapisano." : "Nie zapisano.");
            status.className = "ucho-editor-status " + (data.ok ? "is-ok" : "is-error");
            if (data.ok) dirty = false;
            return data.ok;
        }

        document.getElementById("smr-save").addEventListener("click", () => post({ hidden: [...hidden], custom }));

        // "Zakończ": unsaved changes are saved first, then back to the library
        document.getElementById("smr-done").addEventListener("click", async (e) => {
            if (!dirty) return;
            e.preventDefault();
            if (await post({ hidden: [...hidden], custom })) {
                dirty = false;
                window.location.href = e.currentTarget.href;
            }
        });

        // reset needs a second click (no browser confirm dialogs in the panel)
        const resetBtn = document.getElementById("smr-reset");
        let resetArmed = false;
        resetBtn.addEventListener("click", async () => {
            if (!resetArmed) {
                resetArmed = true;
                resetBtn.textContent = "Na pewno? Kliknij ponownie, aby cofnąć wszystkie zmiany";
                setTimeout(() => {
                    resetArmed = false;
                    resetBtn.textContent = "Przywróć jak w modelu technicznym";
                }, 5000);
                return;
            }
            if (await post({ reset: true })) {
                dirty = false;
                window.location.reload();
            }
        });

        window.addEventListener("beforeunload", (e) => {
            if (!dirty) return;
            e.preventDefault();
            e.returnValue = "";
        });

        setMode(null);
        renderPanel();
    });
})();
