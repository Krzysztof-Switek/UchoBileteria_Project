/*
 * UCHO layout editor — manager arranges tables, rows and chairs on the real
 * hall plan. Uses UchoSeatMap.SeatMap (seatmap.js) in "edit" mode for drawing.
 *
 * Page contract (templates/admin/seating/editor.html):
 *   #sm-editor-host                      — where the editor builds itself
 *   json_script #sm-editor-config        — {room, layout, name, description, urls, usage}
 */
(function () {
    "use strict";

    const M = window.UchoSeatMap;
    const PRESETS = {
        facing5: { label: "Ø1 m · 5 przodem do sceny", make: () => ({ type: "table_round", r: 50, n: 5, arrangement: "facing" }) },
        facing4: { label: "Ø1 m · 4 przodem do sceny", make: () => ({ type: "table_round", r: 50, n: 4, arrangement: "facing" }) },
        round4: { label: "Okrągły · 4", make: () => ({ type: "table_round", r: 45, n: 4 }) },
        round6: { label: "Okrągły · 6", make: () => ({ type: "table_round", r: 55, n: 6 }) },
        round8: { label: "Okrągły · 8", make: () => ({ type: "table_round", r: 70, n: 8 }) },
        round10: { label: "Okrągły · 10", make: () => ({ type: "table_round", r: 85, n: 10 }) },
        rect2: { label: "Kwadratowy · 2", make: () => ({ type: "table_rect", w: 70, h: 70, sides: { top: 1, right: 0, bottom: 1, left: 0 } }) },
        rect4: { label: "Prostokątny · 4", make: () => ({ type: "table_rect", w: 120, h: 80, sides: { top: 2, right: 0, bottom: 2, left: 0 } }) },
        rect6: { label: "Prostokątny · 6", make: () => ({ type: "table_rect", w: 180, h: 80, sides: { top: 3, right: 0, bottom: 3, left: 0 } }) },
        rect10: { label: "Długi · 10", make: () => ({ type: "table_rect", w: 240, h: 80, sides: { top: 4, right: 1, bottom: 4, left: 1 } }) },
        wall3: { label: "Przy ścianie · 3", make: () => ({ type: "table_rect", w: 160, h: 60, sides: { top: 0, right: 0, bottom: 3, left: 0 } }) },
        row10: { label: "Rząd · 10 krzeseł", make: () => ({ type: "row", spacing: 55, n: 10 }) },
        row1: { label: "Pojedyncze krzesło", make: () => ({ type: "row", spacing: 55, n: 1 }) },
        zone: { label: "Strefa / opis", make: () => ({ type: "zone", w: 200, h: 120 }) },
    };
    const SELL_MODES = [
        ["SEATS", "Pojedyncze miejsca"],
        ["TABLE", "Tylko cały stolik"],
        ["BOTH", "Miejsca albo cały stolik"],
    ];
    const ICON_ROUND = '<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="9"/><circle cx="20" cy="5" r="3.5"/><circle cx="35" cy="20" r="3.5"/><circle cx="20" cy="35" r="3.5"/><circle cx="5" cy="20" r="3.5"/></svg>';
    const ICON_RECT = '<svg viewBox="0 0 40 40" aria-hidden="true"><rect x="8" y="14" width="24" height="12" rx="1"/><circle cx="14" cy="8" r="3.5"/><circle cx="26" cy="8" r="3.5"/><circle cx="14" cy="32" r="3.5"/><circle cx="26" cy="32" r="3.5"/></svg>';
    const ICON_ROW = '<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="6" cy="20" r="3.5"/><circle cx="15" cy="20" r="3.5"/><circle cx="24" cy="20" r="3.5"/><circle cx="33" cy="20" r="3.5"/></svg>';
    const ICON_ZONE = '<svg viewBox="0 0 40 40" aria-hidden="true"><rect x="6" y="10" width="28" height="20" stroke-dasharray="3 2" fill="none"/></svg>';

    function clone(x) {
        return JSON.parse(JSON.stringify(x));
    }

    function esc(text) {
        const d = document.createElement("div");
        d.textContent = text == null ? "" : String(text);
        return d.innerHTML;
    }

    function getCookie(name) {
        const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
        return m ? decodeURIComponent(m.pop()) : "";
    }

    function rowLetter(i) {
        let s = "";
        i += 1;
        while (i > 0) {
            const m = (i - 1) % 26;
            s = String.fromCharCode(65 + m) + s;
            i = Math.floor((i - 1) / 26);
        }
        return s;
    }

    class Editor {
        constructor(host, config) {
            this.host = host;
            this.config = config;
            this.room = config.room;
            this.layout = clone(config.layout && config.layout.objects ? config.layout : { schema_version: 1, grid_cm: 100, objects: [] });
            if (![50, 100, 200].includes(this.layout.grid_cm)) this.layout.grid_cm = 100;
            this.showGrid = true;
            this.selection = new Set();
            this.undoStack = [];
            this.redoStack = [];
            this.dirty = false;
            this.snap = true;
            this.problems = { errors: [], warnings: [] };
            this._build();
            this._bindKeys();
            this.refresh(true);
            window.addEventListener("beforeunload", (e) => {
                if (!this.dirty) return;
                e.preventDefault();
                e.returnValue = "";
            });
        }

        // ---- scaffolding -------------------------------------------------

        _build() {
            this.host.innerHTML =
                '<div class="sme-layout">' +
                '  <aside class="sme-palette" aria-label="Paleta">' +
                '    <div class="sme-section-title">Dodaj — kliknij lub przeciągnij na plan</div>' +
                '    <div class="sme-palette-grid"></div>' +
                '    <div class="sme-help">' +
                "      <b>Skróty:</b> przeciąganie — przesuń · Shift+klik — zaznacz kilka · przeciągnij po pustym — ramka · " +
                "      strzałki — przesuń o 10 cm (Shift — o oczko siatki) · R / Shift+R — obróć o 15° · Ctrl+D — powiel · " +
                "      Delete — usuń · Ctrl+Z / Ctrl+Y — cofnij/ponów · dwuklik w krzesło — wyłącz/włącz miejsce · " +
                "      kółko myszy — powiększenie · spacja+przeciągnij — przesuń widok · Alt — bez przyciągania do siatki" +
                "    </div>" +
                "  </aside>" +
                '  <section class="sme-main">' +
                '    <div class="sme-toolbar" role="toolbar">' +
                '      <button type="button" data-cmd="undo" title="Cofnij (Ctrl+Z)">↶ Cofnij</button>' +
                '      <button type="button" data-cmd="redo" title="Ponów (Ctrl+Y)">↷ Ponów</button>' +
                '      <span class="sme-sep"></span>' +
                '      <button type="button" data-cmd="duplicate" title="Powiel (Ctrl+D)">⧉ Powiel</button>' +
                '      <button type="button" data-cmd="rotate" title="Obróć o 15° (R)">⟳ Obróć</button>' +
                '      <button type="button" data-cmd="delete" title="Usuń (Delete)">✕ Usuń</button>' +
                '      <span class="sme-sep"></span>' +
                '      <button type="button" data-cmd="autonumber" title="Stoliki od sceny, od lewej; rzędy A, B, C… od sceny">№ Numeruj automatycznie</button>' +
                '      <label class="sme-check"><input type="checkbox" data-cmd="showgrid" checked> Siatka</label>' +
                '      <select data-cmd="grid" aria-label="Rozmiar oczka siatki"><option value="50">0,5 m</option><option value="100">1 m</option><option value="200">2 m</option></select>' +
                '      <label class="sme-check"><input type="checkbox" data-cmd="snap" checked> Przyciągaj</label>' +
                '      <span class="sme-sep"></span>' +
                '      <button type="button" data-cmd="aim" title="Stoliki z krzesłami od strony widowni (zaznaczone albo wszystkie) — obróć krzesła na scenę">🎭 Skieruj na scenę</button>' +
                '      <button type="button" data-cmd="zones" title="Strefy cenowe tego układu, np. A 120% / B 100% / C 80%">💰 Strefy cenowe…</button>' +
                '      <button type="button" data-cmd="preview" title="Ten sam plan, jaki zobaczy kupujący">👁 Podgląd jak kupujący</button>' +
                "    </div>" +
                '    <div class="sme-canvas-host"></div>' +
                '    <div class="sme-status">' +
                '      <span class="sme-counts"></span>' +
                '      <span class="sme-zone-legend sm-legend"></span>' +
                '      <span class="sme-problems-toggle"></span>' +
                "    </div>" +
                '    <ul class="sme-problems" hidden></ul>' +
                "  </section>" +
                '  <aside class="sme-props" aria-label="Właściwości"></aside>' +
                "</div>" +
                '<div class="sme-modal" hidden><div class="sme-modal-box"></div></div>';

            const palette = this.host.querySelector(".sme-palette-grid");
            Object.entries(PRESETS).forEach(([key, p]) => {
                const b = document.createElement("button");
                b.type = "button";
                b.className = "sme-preset";
                b.draggable = true;
                b.dataset.preset = key;
                const t = p.make().type;
                b.innerHTML = (t === "table_round" ? ICON_ROUND : t === "table_rect" ? ICON_RECT : t === "row" ? ICON_ROW : ICON_ZONE) +
                    "<span>" + esc(p.label) + "</span>";
                b.addEventListener("click", () => this.addPreset(key));
                b.addEventListener("dragstart", (e) => {
                    e.dataTransfer.setData("text/x-ucho-preset", key);
                    e.dataTransfer.effectAllowed = "copy";
                });
                palette.appendChild(b);
            });

            this.map = new M.SeatMap(this.host.querySelector(".sme-canvas-host"), {
                mode: "edit", room: this.room, layout: this.layout,
                onRender: () => this._decorate(),
            });
            this.svg = this.map.svg;
            this._bindCanvas();

            this.host.querySelector(".sme-toolbar").addEventListener("click", (e) => {
                const b = e.target.closest("button[data-cmd]");
                if (b) this.command(b.dataset.cmd);
            });
            this.host.querySelector('[data-cmd="snap"]').addEventListener("change", (e) => {
                this.snap = e.target.checked;
            });
            this.host.querySelector('[data-cmd="showgrid"]').addEventListener("change", (e) => {
                this.showGrid = e.target.checked;
                this.map.drawGrid(this.showGrid ? this.layout.grid_cm : null);
            });
            const gridSelect = this.host.querySelector('[data-cmd="grid"]');
            gridSelect.value = String(this.layout.grid_cm);
            gridSelect.addEventListener("change", (e) => {
                this.commit(() => {
                    this.layout.grid_cm = +e.target.value;
                });
            });
            this.problemsList = this.host.querySelector(".sme-problems");
            this.host.querySelector(".sme-problems-toggle").addEventListener("click", () => {
                this.problemsList.hidden = !this.problemsList.hidden;
            });
            this.problemsList.addEventListener("click", (e) => {
                const li = e.target.closest("li[data-object]");
                if (!li) return;
                this.selection = new Set([li.dataset.object]);
                this.centerOn(li.dataset.object);
                this.refresh();
            });
            this.props = this.host.querySelector(".sme-props");
            this.modal = this.host.querySelector(".sme-modal");
        }

        // ---- data helpers ----------------------------------------------

        obj(id) {
            return this.layout.objects.find((o) => o.id === id);
        }

        selectedObjects() {
            return this.layout.objects.filter((o) => this.selection.has(o.id));
        }

        nextId(prefix) {
            let max = 0;
            this.layout.objects.forEach((o) => {
                const m = new RegExp("^" + prefix + "(\\d+)$").exec(o.id);
                if (m) max = Math.max(max, +m[1]);
            });
            return prefix + (max + 1);
        }

        nextLabel(kind) {
            const used = new Set(this.layout.objects
                .filter((o) => (kind === "row" ? o.type === "row" : o.type === "table_round" || o.type === "table_rect"))
                .map((o) => String(o.label).toLowerCase()));
            for (let i = 0; i < 2000; i++) {
                const label = kind === "row" ? rowLetter(i) : String(i + 1);
                if (!used.has(label.toLowerCase())) return label;
            }
            return "?";
        }

        snapValue(v, force) {
            const g = this.layout.grid_cm || 100;
            return this.snap || force ? Math.round(v / g) * g : Math.round(v);
        }

        /** Screen-direction offset (px-like units) -> canonical delta. */
        screenDelta(dx, dy) {
            return this.map.orientation === "left" ? [-dy, dx] : [dx, dy];
        }

        setSeatCount(obj, n) {
            n = Math.max(0, Math.min(obj.type === "row" ? 60 : 16, n | 0));
            let maxSuffix = 0;
            obj.seats.forEach((s) => {
                const m = /-s(\d+)$/.exec(s.id);
                if (m) maxSuffix = Math.max(maxSuffix, +m[1]);
            });
            while (obj.seats.length < n) {
                maxSuffix += 1;
                obj.seats.push({ id: obj.id + "-s" + maxSuffix, label: "", disabled: false });
            }
            obj.seats.length = n;
            this.relabelSeats(obj);
        }

        relabelSeats(obj) {
            const n = obj.seats.length;
            const start = obj.type === "row" ? (parseInt(obj.start, 10) || 1) : 1;
            obj.seats.forEach((s, j) => {
                const index = obj.type === "row" && obj.numbering === "rtl" ? n - 1 - j : j;
                s.label = String(start + index);
            });
        }

        makeObject(presetKey, x, y) {
            const p = PRESETS[presetKey].make();
            const prefix = p.type === "row" ? "r" : p.type === "zone" ? "z" : "t";
            const obj = { id: this.nextId(prefix), type: p.type, x: this.snapValue(x, true), y: this.snapValue(y, true), rotation: 0 };
            if (p.type === "zone") {
                Object.assign(obj, { label: "Strefa", w: p.w, h: p.h });
                return obj;
            }
            Object.assign(obj, {
                label: this.nextLabel(p.type === "row" ? "row" : "table"),
                sell_mode: "SEATS", category: "", seats: [],
            });
            if (p.type === "table_round") {
                obj.r = p.r;
                obj.arrangement = p.arrangement || "around";
                if (obj.arrangement === "facing") obj.rotation = M.rotationTowardsStage(obj, this.room);
            }
            if (p.type === "table_rect") Object.assign(obj, { w: p.w, h: p.h, sides: Object.assign({}, p.sides) });
            if (p.type === "row") Object.assign(obj, { spacing: p.spacing, numbering: "ltr", start: 1 });
            // In the stage-left view a row should run top-down on screen, i.e. along u.
            const n = p.type === "table_rect" ? Object.values(p.sides).reduce((a, b) => a + b, 0) : p.n;
            this.setSeatCount(obj, n);
            return obj;
        }

        // ---- history -----------------------------------------------------

        snapshot() {
            return JSON.stringify({ layout: this.layout, selection: [...this.selection] });
        }

        commit(mutator, keepPanel) {
            this.undoStack.push(this.snapshot());
            if (this.undoStack.length > 200) this.undoStack.shift();
            this.redoStack = [];
            mutator();
            this.dirty = true;
            this.refresh(!keepPanel);
        }

        restore(state) {
            const s = JSON.parse(state);
            this.layout = s.layout;
            this.map.layout = this.layout;
            this.selection = new Set(s.selection);
            this.dirty = true;
            this.refresh(true);
        }

        undo() {
            if (!this.undoStack.length) return;
            this.redoStack.push(this.snapshot());
            this.restore(this.undoStack.pop());
        }

        redo() {
            if (!this.redoStack.length) return;
            this.undoStack.push(this.snapshot());
            this.restore(this.redoStack.pop());
        }

        // ---- commands ------------------------------------------------------

        addPreset(key, at) {
            const c = at || this.map.viewCenterWorld();
            this.commit(() => {
                const obj = this.makeObject(key, c.x, c.y);
                this.layout.objects.push(obj);
                this.selection = new Set([obj.id]);
            });
        }

        command(cmd) {
            if (cmd === "undo") this.undo();
            else if (cmd === "redo") this.redo();
            else if (cmd === "delete") this.deleteSelected();
            else if (cmd === "duplicate") this.duplicateSelected();
            else if (cmd === "rotate") this.rotateSelected(15);
            else if (cmd === "autonumber") this.autoNumber();
            else if (cmd === "preview") this.preview();
            else if (cmd === "zones") this.editZones();
            else if (cmd === "aim") this.aimAtStage(this.selection.size ? this.selectedObjects() : this.layout.objects);
        }

        deleteSelected() {
            if (!this.selection.size) return;
            this.commit(() => {
                this.layout.objects = this.layout.objects.filter((o) => !this.selection.has(o.id));
                this.map.layout = this.layout;
                this.selection.clear();
            });
        }

        cloneObject(src, dx, dy) {
            const prefix = src.type === "row" ? "r" : src.type === "zone" ? "z" : "t";
            const copy = clone(src);
            copy.id = this.nextId(prefix);
            copy.x = src.x + dx;
            copy.y = src.y + dy;
            if (M.isSellable(copy)) {
                copy.label = this.nextLabel(copy.type === "row" ? "row" : "table");
                copy.seats = src.seats.map((s, i) => ({ id: copy.id + "-s" + (i + 1), label: s.label, disabled: s.disabled }));
            }
            return copy;
        }

        duplicateSelected() {
            const objs = this.selectedObjects();
            if (!objs.length) return;
            const [dx, dy] = this.screenDelta(60, 60);
            this.commit(() => {
                const fresh = objs.map((o) => {
                    const copy = this.cloneObject(o, dx, dy);
                    this.layout.objects.push(copy);
                    return copy.id;
                });
                this.selection = new Set(fresh);
            });
        }

        duplicateGrid(cols, rows, gapX, gapY) {
            const src = this.selectedObjects()[0];
            if (!src) return;
            this.commit(() => {
                const fresh = [src.id];
                for (let r = 0; r < rows; r++) {
                    for (let c = 0; c < cols; c++) {
                        if (!r && !c) continue;
                        const [dx, dy] = this.screenDelta(c * gapX, r * gapY);
                        const copy = this.cloneObject(src, dx, dy);
                        this.layout.objects.push(copy);
                        fresh.push(copy.id);
                    }
                }
                this.selection = new Set(fresh);
            });
        }

        rotateSelected(deg) {
            const objs = this.selectedObjects();
            if (!objs.length) return;
            this.commit(() => objs.forEach((o) => {
                o.rotation = (((+o.rotation || 0) + deg) % 360 + 360) % 360;
            }), true);
        }

        moveSelected(dx, dy) {
            const objs = this.selectedObjects();
            if (!objs.length) return;
            const [du, dv] = this.screenDelta(dx, dy);
            this.commit(() => objs.forEach((o) => {
                o.x = Math.round(o.x + du);
                o.y = Math.round(o.y + dv);
            }), true);
        }

        autoNumber() {
            const tables = this.layout.objects.filter((o) => o.type === "table_round" || o.type === "table_rect");
            const rows = this.layout.objects.filter((o) => o.type === "row");
            // stage at the top of the canonical plan: smaller v = closer to the stage;
            // within one "line" (±80 cm) from the audience's left to right (u up).
            const ordered = (list) => {
                const sorted = list.slice().sort((a, b) => a.y - b.y);
                const lines = [];
                sorted.forEach((o) => {
                    const line = lines.find((l) => Math.abs(l[0].y - o.y) <= 80);
                    if (line) line.push(o);
                    else lines.push([o]);
                });
                return lines.flatMap((l) => l.sort((a, b) => a.x - b.x));
            };
            this.commit(() => {
                ordered(tables).forEach((o, i) => {
                    o.label = String(i + 1);
                });
                rows.slice().sort((a, b) => a.y - b.y || a.x - b.x).forEach((o, i) => {
                    o.label = rowLetter(i);
                });
                tables.forEach((o) => this.relabelSeats(o));
            });
        }

        align(how) {
            const objs = this.selectedObjects();
            if (objs.length < 2) return;
            const proj = (o) => this.map.project(o.x, o.y);
            const pts = objs.map(proj);
            const xs = pts.map((p) => p[0]);
            const ys = pts.map((p) => p[1]);
            this.commit(() => {
                objs.forEach((o, i) => {
                    let [sx, sy] = pts[i];
                    if (how === "left") sx = Math.min(...xs);
                    if (how === "hcenter") sx = (Math.min(...xs) + Math.max(...xs)) / 2;
                    if (how === "top") sy = Math.min(...ys);
                    if (how === "vcenter") sy = (Math.min(...ys) + Math.max(...ys)) / 2;
                    this._setScreenPos(o, sx, sy);
                });
                if (how === "hdist" || how === "vdist") {
                    const axis = how === "hdist" ? 0 : 1;
                    const order = objs.map((o, i) => [o, pts[i]]).sort((a, b) => a[1][axis] - b[1][axis]);
                    const first = order[0][1][axis];
                    const last = order[order.length - 1][1][axis];
                    order.forEach(([o, p], i) => {
                        const value = first + ((last - first) * i) / (order.length - 1);
                        this._setScreenPos(o, axis === 0 ? value : p[0], axis === 1 ? value : p[1]);
                    });
                }
            }, true);
        }

        _setScreenPos(o, sx, sy) {
            if (this.map.orientation === "left") {
                o.y = Math.round(sx);
                o.x = Math.round(this.room.width_cm - sy);
            } else {
                o.x = Math.round(sx);
                o.y = Math.round(sy);
            }
        }

        centerOn(id) {
            const o = this.obj(id);
            if (!o) return;
            const [sx, sy] = this.map.project(o.x, o.y);
            const v = this.map.view;
            this.map.view = Object.assign({}, v, { x: sx - v.w / 2, y: sy - v.h / 2 });
            this.map._applyView();
        }

        // ---- canvas interaction ---------------------------------------------

        _bindCanvas() {
            const svg = this.svg;
            let drag = null;
            let spaceDown = false;
            document.addEventListener("keydown", (e) => {
                if (e.code === "Space" && !this._typing(e)) {
                    spaceDown = true;
                    svg.classList.add("is-panning");
                    e.preventDefault();
                }
            });
            document.addEventListener("keyup", (e) => {
                if (e.code === "Space") {
                    spaceDown = false;
                    svg.classList.remove("is-panning");
                }
            });

            svg.addEventListener("pointerdown", (e) => {
                const world = this.map.clientToWorld(e.clientX, e.clientY);
                if (e.button === 1 || (e.button === 0 && spaceDown)) {
                    drag = { kind: "pan", x: e.clientX, y: e.clientY, view: Object.assign({}, this.map.view) };
                } else if (e.button === 0) {
                    const hit = e.target.closest("[data-object]");
                    if (hit) {
                        const id = hit.dataset.object;
                        if (e.shiftKey || e.ctrlKey || e.metaKey) {
                            if (this.selection.has(id)) this.selection.delete(id);
                            else this.selection.add(id);
                            this.refresh(true);
                            return;
                        }
                        if (!this.selection.has(id)) {
                            this.selection = new Set([id]);
                            this.refresh(true);
                        }
                        drag = {
                            kind: "move", start: world, moved: false, alt: e.altKey,
                            origin: this.selectedObjects().map((o) => ({ o, x: o.x, y: o.y })),
                        };
                    } else {
                        drag = { kind: "marquee", start: world, add: e.shiftKey };
                    }
                } else {
                    return;
                }
                svg.setPointerCapture(e.pointerId);
                e.preventDefault();
            });

            svg.addEventListener("pointermove", (e) => {
                if (!drag) return;
                if (drag.kind === "pan") {
                    const k = this.map.pixelsPerUnit();
                    this.map.view = Object.assign({}, drag.view, {
                        x: drag.view.x - (e.clientX - drag.x) / k,
                        y: drag.view.y - (e.clientY - drag.y) / k,
                    });
                    this.map._applyView();
                    return;
                }
                const world = this.map.clientToWorld(e.clientX, e.clientY);
                const dx = world.x - drag.start.x;
                const dy = world.y - drag.start.y;
                if (drag.kind === "move") {
                    if (!drag.moved && Math.hypot(dx, dy) * this.map.pixelsPerUnit() < 3) return;
                    if (!drag.moved) {
                        drag.moved = true;
                        drag.before = this.snapshot();
                    }
                    const free = drag.alt || e.altKey || !this.snap;
                    const lead = drag.origin[0];
                    const nx = free ? lead.x + dx : this.snapValue(lead.x + dx, true);
                    const ny = free ? lead.y + dy : this.snapValue(lead.y + dy, true);
                    const ddx = nx - lead.x;
                    const ddy = ny - lead.y;
                    drag.origin.forEach(({ o }) => {
                        const node = this.map.objectNodes.get(o.id);
                        if (node) node.setAttribute("transform", "translate(" + ddx.toFixed(1) + " " + ddy.toFixed(1) + ")");
                    });
                    drag.delta = [ddx, ddy];
                } else if (drag.kind === "marquee") {
                    const x1 = Math.min(drag.start.x, world.x);
                    const y1 = Math.min(drag.start.y, world.y);
                    if (!drag.rect) drag.rect = M.el("rect", { class: "sme-marquee" }, this.map.overlay);
                    drag.rect.setAttribute("x", x1);
                    drag.rect.setAttribute("y", y1);
                    drag.rect.setAttribute("width", Math.abs(dx));
                    drag.rect.setAttribute("height", Math.abs(dy));
                    drag.box = { x1, y1, x2: x1 + Math.abs(dx), y2: y1 + Math.abs(dy) };
                }
            });

            const finish = () => {
                if (!drag) return;
                const d = drag;
                drag = null;
                if (d.kind === "move" && d.moved && d.delta) {
                    this.undoStack.push(d.before);
                    this.redoStack = [];
                    d.origin.forEach(({ o, x, y }) => {
                        o.x = Math.round(x + d.delta[0]);
                        o.y = Math.round(y + d.delta[1]);
                        if (o.type === "table_round" && o.arrangement === "facing" && !o.manual_rotation) {
                            o.rotation = M.rotationTowardsStage(o, this.room);
                        }
                    });
                    this.dirty = true;
                    this.refresh(true);
                } else if (d.kind === "marquee") {
                    if (d.rect) d.rect.remove();
                    if (d.box && (d.box.x2 - d.box.x1 > 5 || d.box.y2 - d.box.y1 > 5)) {
                        const inside = this.layout.objects.filter((o) => o.x >= d.box.x1 && o.x <= d.box.x2 && o.y >= d.box.y1 && o.y <= d.box.y2);
                        if (!d.add) this.selection.clear();
                        inside.forEach((o) => this.selection.add(o.id));
                    } else if (!d.add) {
                        this.selection.clear();
                    }
                    this.refresh(true);
                }
            };
            svg.addEventListener("pointerup", finish);
            svg.addEventListener("pointercancel", finish);

            svg.addEventListener("dblclick", (e) => {
                const seatNode = e.target.closest(".sm-seat");
                if (!seatNode) return;
                const o = this.obj(seatNode.dataset.object);
                const seat = o && o.seats.find((s) => s.id === seatNode.dataset.seat);
                if (!seat) return;
                this.commit(() => {
                    seat.disabled = !seat.disabled;
                });
            });

            const host = this.map.wrap;
            host.addEventListener("dragover", (e) => {
                if (e.dataTransfer.types.includes("text/x-ucho-preset")) {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = "copy";
                }
            });
            host.addEventListener("drop", (e) => {
                const key = e.dataTransfer.getData("text/x-ucho-preset");
                if (!key) return;
                e.preventDefault();
                this.addPreset(key, this.map.clientToWorld(e.clientX, e.clientY));
            });
        }

        _typing(e) {
            const t = e.target;
            return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
        }

        _bindKeys() {
            document.addEventListener("keydown", (e) => {
                if (this._typing(e) || !this.modal.hidden) return;
                const ctrl = e.ctrlKey || e.metaKey;
                // arrows: fine 10 cm nudge; with Shift a whole grid step
                const step = e.shiftKey ? (this.layout.grid_cm || 100) : 10;
                let handled = true;
                if (ctrl && e.key.toLowerCase() === "z" && !e.shiftKey) this.undo();
                else if (ctrl && (e.key.toLowerCase() === "y" || (e.key.toLowerCase() === "z" && e.shiftKey))) this.redo();
                else if (ctrl && e.key.toLowerCase() === "d") this.duplicateSelected();
                else if (ctrl && e.key.toLowerCase() === "a") {
                    this.selection = new Set(this.layout.objects.map((o) => o.id));
                    this.refresh(true);
                } else if (e.key === "Delete" || e.key === "Backspace") this.deleteSelected();
                else if (e.key === "Escape") {
                    this.selection.clear();
                    this.refresh(true);
                } else if (e.key.toLowerCase() === "r" && !ctrl) this.rotateSelected(e.shiftKey ? -15 : 15);
                else if (e.key === "ArrowLeft") this.moveSelected(-step, 0);
                else if (e.key === "ArrowRight") this.moveSelected(step, 0);
                else if (e.key === "ArrowUp") this.moveSelected(0, -step);
                else if (e.key === "ArrowDown") this.moveSelected(0, step);
                else handled = false;
                if (handled) e.preventDefault();
            });
        }

        // ---- rendering ----------------------------------------------------------

        refresh(rebuildPanel) {
            this.map.layout = this.layout;
            this.map.render();
            this.map.drawGrid(this.showGrid ? this.layout.grid_cm : null);
            const gridSelect = this.host.querySelector('[data-cmd="grid"]');
            if (gridSelect && gridSelect.value !== String(this.layout.grid_cm)) gridSelect.value = String(this.layout.grid_cm);
            this.validate();
            this._decorate();
            this._status();
            if (rebuildPanel) this.renderPanel();
            this.host.querySelector('[data-cmd="undo"]').disabled = !this.undoStack.length;
            this.host.querySelector('[data-cmd="redo"]').disabled = !this.redoStack.length;
            ["duplicate", "rotate", "delete"].forEach((c) => {
                this.host.querySelector('[data-cmd="' + c + '"]').disabled = !this.selection.size;
            });
            if (this.config.onDirty) this.config.onDirty(this.dirty);
        }

        _decorate() {
            if (!this.map || !this.problems) return; // first render runs inside the SeatMap constructor
            const bad = new Set([...this.problems.errors, ...this.problems.warnings].map((p) => p.object));
            this.map.objectNodes.forEach((node, id) => {
                node.classList.toggle("is-selected", this.selection.has(id));
                node.classList.toggle("has-problem", bad.has(id));
            });
        }

        _status() {
            const seats = M.expandSeats(this.layout);
            const sellable = seats.filter((s) => !s.disabled).length;
            const tables = this.layout.objects.filter((o) => o.type === "table_round" || o.type === "table_rect").length;
            const rows = this.layout.objects.filter((o) => o.type === "row").length;
            this.host.querySelector(".sme-counts").innerHTML =
                "Miejsc do sprzedaży: <b>" + sellable + "</b>" +
                (seats.length - sellable ? " (+" + (seats.length - sellable) + " wyłączonych)" : "") +
                " · Stolików: <b>" + tables + "</b> · Rzędów/krzeseł: <b>" + rows + "</b>";
            // price zones: colour, %, how many seats each — so it's clear what is cheaper
            const zones = this.layout.zones || [];
            const perZone = {};
            seats.filter((s) => !s.disabled).forEach((s) => {
                perZone[s.obj.category || ""] = (perZone[s.obj.category || ""] || 0) + 1;
            });
            this.host.querySelector(".sme-zone-legend").innerHTML = zones.map((z, i) =>
                '<span class="sm-z' + i + '"><i class="lg-zone"></i><b>' + esc(z.code) + "</b> " + z.percent + "%" +
                (z.name ? " " + esc(z.name) : "") + " · " + (perZone[z.code] || 0) + " m.</span>").join("") +
                (zones.length && perZone[""] ? "<span>bez strefy (100%): " + perZone[""] + " m.</span>" : "");
            const e = this.problems.errors.length;
            const w = this.problems.warnings.length;
            const toggle = this.host.querySelector(".sme-problems-toggle");
            toggle.className = "sme-problems-toggle " + (e ? "is-error" : w ? "is-warning" : "is-ok");
            toggle.textContent = e ? "✕ " + e + " błęd(y) do poprawienia" + (w ? ", ⚠ " + w + " ostrzeżeń" : "")
                : w ? "⚠ " + w + " ostrzeżeń — kliknij, by zobaczyć" : "✓ brak problemów";
            this.problemsList.innerHTML = [...this.problems.errors.map((p) => [p, "error"]), ...this.problems.warnings.map((p) => [p, "warning"])]
                .map(([p, kind]) => '<li class="is-' + kind + '"' + (p.object ? ' data-object="' + esc(p.object) + '"' : "") + ">" +
                    (kind === "error" ? "✕ " : "⚠ ") + esc(p.message) + "</li>").join("");
            if (!e && !w) this.problemsList.hidden = true;
        }

        // ---- validation (twin of layout.py, for live feedback) -------------------

        validate() {
            const errors = [];
            const warnings = [];
            const err = (o, m) => errors.push({ object: o ? o.id : null, message: m });
            const warn = (o, m) => {
                if (!warnings.some((w) => w.object === o.id && w.message === m)) warnings.push({ object: o.id, message: m });
            };
            const seen = { table: new Map(), row: new Map() };
            let sellable = 0;
            this.layout.objects.forEach((o) => {
                const name = M.groupName(o);
                if (!String(o.label || "").trim()) err(o, name + ": brak numeru/nazwy.");
                if (String(o.label || "").length > 10) err(o, name + ": numer/nazwa może mieć najwyżej 10 znaków.");
                if (!M.isSellable(o)) return;
                const bucket = o.type === "row" ? "row" : "table";
                const key = String(o.label).trim().toLowerCase();
                if (seen[bucket].has(key)) err(o, "Zdublowany numer: " + name + ".");
                seen[bucket].set(key, o);
                if (!o.seats.length) err(o, name + ": brak miejsc.");
                if (o.type === "table_round" && o.arrangement === "facing" && !M.facingFits(+o.r, o.seats.length)) {
                    err(o, name + ": tyle krzeseł nie zmieści się od strony widowni — mniej miejsc albo większy stolik.");
                }
                const labels = new Set();
                o.seats.forEach((s) => {
                    const l = String(s.label).trim().toLowerCase();
                    if (!l) err(o, name + ": miejsce bez numeru.");
                    else if (labels.has(l)) err(o, name + ": zdublowany numer miejsca " + s.label + ".");
                    labels.add(l);
                    if (!s.disabled) sellable += 1;
                });
            });
            if (!sellable && this.layout.objects.length) err(null, "Układ musi mieć co najmniej jedno miejsce do sprzedaży.");

            const room = this.room;
            const obstacles = (room.obstacles || []).filter((o) => o.blocking);
            const allSeats = [];
            this.layout.objects.forEach((o) => {
                const name = M.groupName(o);
                const pos = M.isSellable(o) ? M.seatPositions(o) : [];
                const checks = pos.map(([x, y]) => [x, y, M.CHAIR_R]);
                if (o.type === "table_round") checks.push([o.x, o.y, o.r]);
                const poly = M.objectPolygon(o);
                if (poly) poly.forEach(([x, y]) => checks.push([x, y, 1]));
                pos.forEach(([x, y]) => allSeats.push([o, x, y]));
                if (room.outline && checks.some(([x, y]) => !M.pointInPolygon(x, y, room.outline))) warn(o, name + " wychodzi poza obrys sali.");
                checks.forEach(([x, y, r]) => obstacles.forEach((ob) => {
                    if (M.circleHitsShape(x, y, r, ob)) warn(o, name + " koliduje z: " + ob.name + ".");
                }));
                if (poly && o.type === "table_rect") {
                    obstacles.forEach((ob) => {
                        if (ob.shape === "circle" && M.pointInPolygon(ob.cx, ob.cy, poly)) warn(o, name + " koliduje z: " + ob.name + ".");
                    });
                }
                pos.forEach(([x, y]) => {
                    (room.entrances || []).forEach((en) => {
                        if (M.circleHitsPolygon(x, y, M.CHAIR_R + (en.clearance || 100), en.points)) warn(o, name + " zastawia przejście: " + en.name + ".");
                    });
                    if (room.pit && M.circleHitsPolygon(x, y, M.CHAIR_R, room.pit.points)) warn(o, name + " stoi w fosie przed sceną (za barierkami).");
                });
            });
            const tables = this.layout.objects.filter((o) => o.type === "table_round" || o.type === "table_rect");
            for (let i = 0; i < allSeats.length; i++) {
                const [o, x, y] = allSeats[i];
                for (let j = i + 1; j < allSeats.length; j++) {
                    const [o2, x2, y2] = allSeats[j];
                    if (o2 !== o && Math.abs(x - x2) < 40 && Math.abs(y - y2) < 40 && Math.hypot(x - x2, y - y2) < 2 * M.CHAIR_R - 4) {
                        warn(o, "Krzesła nachodzą na siebie: " + M.groupName(o) + " i " + M.groupName(o2) + ".");
                    }
                }
                tables.forEach((t) => {
                    if (t === o) return;
                    const hit = t.type === "table_round"
                        ? Math.hypot(x - t.x, y - t.y) < t.r + M.CHAIR_R - 4
                        : M.circleHitsPolygon(x, y, M.CHAIR_R - 4, M.objectPolygon(t));
                    if (hit) warn(o, M.groupName(o) + " nachodzi na " + M.groupName(t) + ".");
                });
            }
            this.problems = { errors, warnings };
        }

        // ---- properties panel ------------------------------------------------------

        renderPanel() {
            const objs = this.selectedObjects();
            const p = this.props;
            if (!objs.length) {
                p.innerHTML = '<div class="sme-section-title">Właściwości</div>' +
                    '<p class="sme-muted">Kliknij stolik, rząd lub strefę na planie, aby zmienić numer, liczbę miejsc, ' +
                    "wymiary, obrót i sposób sprzedaży. Przeciągnij po pustym miejscu, aby zaznaczyć kilka obiektów.</p>" +
                    '<p class="sme-muted">Szare elementy to stałe elementy sali z pomiarów (scena, bar, słupy, schody, ' +
                    "wejścia) — tych nie da się przesunąć. Linia przerywana to antresola nad głową.</p>" +
                    (this.room.unknown && this.room.unknown.length
                        ? '<p class="sme-muted">Bez znanego położenia (sprawdź na miejscu): ' + esc(this.room.unknown.join(", ")) + ".</p>" : "");
                return;
            }
            if (objs.length > 1) {
                p.innerHTML = '<div class="sme-section-title">Zaznaczono: ' + objs.length + "</div>" +
                    '<div class="sme-btn-grid">' +
                    '<button type="button" data-align="left">⇤ Wyrównaj do lewej</button>' +
                    '<button type="button" data-align="hcenter">↔ Środek poziomo</button>' +
                    '<button type="button" data-align="top">⤒ Wyrównaj do góry</button>' +
                    '<button type="button" data-align="vcenter">↕ Środek pionowo</button>' +
                    '<button type="button" data-align="hdist">⇹ Rozłóż równo poziomo</button>' +
                    '<button type="button" data-align="vdist">⇳ Rozłóż równo pionowo</button>' +
                    "</div>" +
                    '<label class="sme-field">Sprzedaż (wszystkie stoliki)<select data-bulk="sell_mode"><option value="">— bez zmian —</option>' +
                    SELL_MODES.map(([v, l]) => '<option value="' + v + '">' + l + "</option>").join("") + "</select></label>" +
                    '<label class="sme-field">Strefa cenowa (wszystkie)' + this._zoneSelect("", 'data-bulk="category"', true) + "</label>" +
                    '<button type="button" class="sme-danger" data-cmd2="delete">✕ Usuń zaznaczone</button>';
                p.querySelectorAll("[data-align]").forEach((b) => b.addEventListener("click", () => this.align(b.dataset.align)));
                p.querySelector('[data-bulk="sell_mode"]').addEventListener("change", (e) => {
                    if (!e.target.value) return;
                    this.commit(() => objs.forEach((o) => {
                        if (o.type === "table_round" || o.type === "table_rect") o.sell_mode = e.target.value;
                    }));
                });
                p.querySelector('[data-bulk="category"]').addEventListener("change", (e) => {
                    if (e.target.value === "__keep") return;
                    this.commit(() => objs.forEach((o) => {
                        if (M.isSellable(o)) o.category = e.target.value;
                    }));
                });
                p.querySelector('[data-cmd2="delete"]').addEventListener("click", () => this.deleteSelected());
                return;
            }

            const o = objs[0];
            const isTable = o.type === "table_round" || o.type === "table_rect";
            const typeName = { table_round: "Stolik okrągły", table_rect: "Stolik prostokątny", row: "Rząd krzeseł", zone: "Strefa / opis" }[o.type];
            let html = '<div class="sme-section-title">' + typeName + "</div>";
            html += this._field(o.type === "row" ? "Rząd (litera/numer)" : o.type === "zone" ? "Napis" : "Numer stolika", "label", o.label, "text", 'maxlength="10"');
            if (o.type === "table_round") {
                html += '<label class="sme-field">Krzesła<select data-field="arrangement">' +
                    '<option value="facing"' + (o.arrangement === "facing" ? " selected" : "") + ">od strony widowni — wszyscy widzą scenę</option>" +
                    '<option value="around"' + (o.arrangement !== "facing" ? " selected" : "") + ">wokół stołu</option></select></label>";
                if (o.arrangement === "facing") {
                    html += '<button type="button" data-cmd2="aim">🎭 Skieruj krzesła na scenę</button>';
                }
                html += this._field("Liczba miejsc", "count", o.seats.length, "number", 'min="1" max="16"');
                html += this._field("Średnica blatu (cm)", "diameter", o.r * 2, "number", 'min="40" max="400" step="5"');
            }
            if (o.type === "table_rect") {
                html += '<div class="sme-field">Miejsca na bokach <span class="sme-muted">(góra · prawo · dół · lewo, w układzie stolika)</span><div class="sme-sides">' +
                    ["top", "right", "bottom", "left"].map((s) => '<input type="number" min="0" max="12" data-side="' + s + '" value="' + (o.sides[s] || 0) + '" aria-label="' + s + '">').join("") +
                    "</div></div>";
            }
            if (o.type === "table_rect" || o.type === "zone") {
                html += '<div class="sme-field">Wymiary blatu (cm)<div class="sme-pair">' +
                    '<input type="number" min="30" max="1200" step="5" data-field="w" value="' + o.w + '" aria-label="długość">×' +
                    '<input type="number" min="30" max="1200" step="5" data-field="h" value="' + o.h + '" aria-label="szerokość"></div></div>';
            }
            if (o.type === "row") {
                html += this._field("Liczba krzeseł", "count", o.seats.length, "number", 'min="1" max="60"');
                html += this._field("Rozstaw krzeseł (cm)", "spacing", o.spacing, "number", 'min="40" max="200" step="5"');
                html += this._field("Numer pierwszego miejsca", "start", o.start || 1, "number", 'min="0" max="999"');
                html += '<label class="sme-field">Numeracja<select data-field="numbering">' +
                    '<option value="ltr"' + (o.numbering !== "rtl" ? " selected" : "") + ">od lewej do prawej</option>" +
                    '<option value="rtl"' + (o.numbering === "rtl" ? " selected" : "") + ">od prawej do lewej</option></select></label>";
            }
            html += this._field("Obrót (°)", "rotation", o.rotation || 0, "number", 'min="-360" max="360" step="5"');
            if (isTable) {
                html += '<fieldset class="sme-field"><legend>Sprzedaż</legend>' + SELL_MODES.map(([v, l]) =>
                    '<label class="sme-radio"><input type="radio" name="sme-sell" value="' + v + '"' + ((o.sell_mode || "SEATS") === v ? " checked" : "") + "> " + l + "</label>").join("") +
                    "</fieldset>";
            }
            if (M.isSellable(o)) {
                html += (this.layout.zones || []).length
                    ? '<label class="sme-field">Strefa cenowa' + this._zoneSelect(o.category || "", 'data-field="category"') + "</label>"
                    : '<div class="sme-field">Strefa cenowa <span class="sme-muted">— układ nie ma stref (wszystkie miejsca w cenie puli).</span>' +
                      '<button type="button" data-cmd2="zones">💰 Ustaw strefy cenowe…</button></div>';
                html += '<div class="sme-field">Miejsca <span class="sme-muted">— kliknij, aby wyłączyć/włączyć (np. krzesło przy ścianie)</span><div class="sme-seat-chips">' +
                    o.seats.map((s) => '<button type="button" class="sme-chip' + (s.disabled ? " is-off" : "") + '" data-seat="' + esc(s.id) + '" title="' + (s.disabled ? "wyłączone — kliknij, aby włączyć" : "kliknij, aby wyłączyć") + '">' + esc(s.label) + "</button>").join("") +
                    "</div></div>";
            }
            html += '<details class="sme-field"><summary>Powiel w siatce…</summary>' +
                '<div class="sme-grid-form">' +
                '<label>Kolumn<input type="number" min="1" max="20" value="3" data-grid="cols"></label>' +
                '<label>Rzędów<input type="number" min="1" max="20" value="2" data-grid="rows"></label>' +
                '<label>Odstęp poziomo (cm)<input type="number" min="40" max="2000" step="10" value="' + this._suggestGap(o, 0) + '" data-grid="gx"></label>' +
                '<label>Odstęp pionowo (cm)<input type="number" min="40" max="2000" step="10" value="' + this._suggestGap(o, 1) + '" data-grid="gy"></label>' +
                '<button type="button" data-grid="go">Utwórz kopie</button></div>' +
                '<p class="sme-muted">Kolejne stoliki dostaną kolejne wolne numery; potem możesz użyć „Numeruj automatycznie”.</p></details>';
            html += '<button type="button" class="sme-danger" data-cmd2="delete">✕ Usuń</button>';
            p.innerHTML = html;

            p.querySelectorAll("[data-field]").forEach((input) => {
                input.addEventListener("change", () => this._applyField(o, input.dataset.field, input.value));
            });
            p.querySelectorAll("[data-side]").forEach((input) => {
                input.addEventListener("change", () => {
                    this.commit(() => {
                        o.sides[input.dataset.side] = Math.max(0, Math.min(12, parseInt(input.value, 10) || 0));
                        const total = Object.values(o.sides).reduce((a, b) => a + b, 0);
                        this.setSeatCount(o, total);
                    });
                });
            });
            p.querySelectorAll('input[name="sme-sell"]').forEach((r) => r.addEventListener("change", () => {
                this.commit(() => {
                    o.sell_mode = r.value;
                });
            }));
            p.querySelectorAll(".sme-chip").forEach((chip) => chip.addEventListener("click", () => {
                const seat = o.seats.find((s) => s.id === chip.dataset.seat);
                this.commit(() => {
                    seat.disabled = !seat.disabled;
                });
            }));
            const go = p.querySelector('[data-grid="go"]');
            go.addEventListener("click", () => {
                const val = (k) => parseInt(p.querySelector('[data-grid="' + k + '"]').value, 10) || 0;
                const cols = Math.max(1, Math.min(20, val("cols")));
                const rows = Math.max(1, Math.min(20, val("rows")));
                this.duplicateGrid(cols, rows, val("gx"), val("gy"));
            });
            p.querySelector('[data-cmd2="delete"]').addEventListener("click", () => this.deleteSelected());
            const aimBtn = p.querySelector('[data-cmd2="aim"]');
            if (aimBtn) aimBtn.addEventListener("click", () => this.aimAtStage([o]));
            const zonesBtn = p.querySelector('[data-cmd2="zones"]');
            if (zonesBtn) zonesBtn.addEventListener("click", () => this.editZones());
        }

        /** Turn "facing" tables so their chairs look at the stage. */
        aimAtStage(objs) {
            const targets = objs.filter((o) => o.type === "table_round" && o.arrangement === "facing");
            if (!targets.length) return;
            this.commit(() => targets.forEach((o) => {
                delete o.manual_rotation;
                o.rotation = M.rotationTowardsStage(o, this.room);
            }), true);
        }

        _zoneSelect(current, attrs, bulk) {
            const zones = this.layout.zones || [];
            return "<select " + attrs + ">" +
                (bulk ? '<option value="__keep">— bez zmian —</option>' : "") +
                '<option value=""' + (current === "" && !bulk ? " selected" : "") + ">bez strefy (100%)</option>" +
                zones.map((z) => '<option value="' + esc(z.code) + '"' + (z.code === current ? " selected" : "") + ">" +
                    esc(z.code) + " — " + z.percent + "%" + (z.name ? " · " + esc(z.name) : "") + "</option>").join("") +
                "</select>";
        }

        /** Modal: the layout's price zones (code, name, % of the pool price). */
        editZones() {
            let rows = clone(this.layout.zones || []);
            const close = this.openModal('<div class="sme-modal-head"><b>Strefy cenowe układu</b>' +
                '<span class="sme-muted">— cena miejsca = cena aktywnej puli × procent strefy (100% = cena puli; inne zaokrąglane do pełnych złotych)</span>' +
                '<button type="button" data-close>Anuluj</button></div>' +
                '<table class="sme-zone-table"><thead><tr><th></th><th>Kod</th><th>Nazwa (dla kupujących)</th><th>% ceny</th><th></th></tr></thead><tbody></tbody></table>' +
                '<p class="sme-zone-error" role="alert"></p>' +
                '<div class="sme-modal-actions">' +
                '<button type="button" data-z="preset">Wstaw A 120% · B 100% · C 80%</button>' +
                '<button type="button" data-z="add">+ Dodaj strefę</button>' +
                '<button type="button" class="sme-primary" data-z="save">Zastosuj</button></div>');
            const box = this.modal.querySelector(".sme-modal-box");
            const body = box.querySelector("tbody");
            const error = box.querySelector(".sme-zone-error");
            const draw = () => {
                body.innerHTML = rows.map((z, i) => '<tr class="sm-z' + i + '">' +
                    '<td><i class="lg-zone sme-zone-dot"></i></td>' +
                    '<td><input data-zi="' + i + '" data-zk="code" value="' + esc(z.code) + '" maxlength="3" size="3"></td>' +
                    '<td><input data-zi="' + i + '" data-zk="name" value="' + esc(z.name || "") + '" maxlength="40" placeholder="np. przy scenie"></td>' +
                    '<td><input data-zi="' + i + '" data-zk="percent" type="number" min="10" max="500" step="5" value="' + z.percent + '"></td>' +
                    '<td><button type="button" data-zdel="' + i + '" title="Usuń strefę">✕</button></td></tr>').join("") ||
                    '<tr><td colspan="5" class="sme-muted">Brak stref — wszystkie miejsca w cenie puli.</td></tr>';
            };
            draw();
            body.addEventListener("input", (e) => {
                const i = e.target.dataset.zi;
                if (i === undefined) return;
                const key = e.target.dataset.zk;
                rows[i][key] = key === "percent" ? parseInt(e.target.value, 10) : key === "code" ? e.target.value.toUpperCase().trim() : e.target.value;
            });
            body.addEventListener("click", (e) => {
                const b = e.target.closest("[data-zdel]");
                if (!b) return;
                rows.splice(+b.dataset.zdel, 1);
                draw();
            });
            box.querySelector('[data-z="preset"]').addEventListener("click", () => {
                rows = [{ code: "A", name: "najlepsze miejsca", percent: 120 },
                    { code: "B", name: "standard", percent: 100 },
                    { code: "C", name: "tańsze", percent: 80 }];
                draw();
            });
            box.querySelector('[data-z="add"]').addEventListener("click", () => {
                if (rows.length >= 6) return;
                const used = new Set(rows.map((z) => z.code));
                const code = "ABCDEF".split("").find((c) => !used.has(c)) || "X";
                rows.push({ code, name: "", percent: 100 });
                draw();
            });
            box.querySelector('[data-z="save"]').addEventListener("click", () => {
                const codes = new Set();
                for (const z of rows) {
                    if (!/^[A-Z0-9]{1,3}$/.test(z.code || "")) return (error.textContent = "Kod strefy: 1–3 wielkie litery lub cyfry.");
                    if (codes.has(z.code)) return (error.textContent = "Zdublowany kod strefy " + z.code + ".");
                    if (!(z.percent >= 10 && z.percent <= 500)) return (error.textContent = "Strefa " + z.code + ": procent 10–500.");
                    codes.add(z.code);
                }
                this.commit(() => {
                    this.layout.zones = rows.map((z) => ({ code: z.code, name: (z.name || "").trim(), percent: z.percent }));
                    // tables of a removed zone fall back to the base price
                    this.layout.objects.forEach((o) => {
                        if (M.isSellable(o) && o.category && !codes.has(o.category)) o.category = "";
                    });
                });
                close();
            });
        }

        _suggestGap(o, axis) {
            const b = M.objectBounds(o);
            const [w, h] = this.map.orientation === "left" ? [b.y2 - b.y1, b.x2 - b.x1] : [b.x2 - b.x1, b.y2 - b.y1];
            return Math.ceil(((axis === 0 ? w : h) + 60) / 10) * 10;
        }

        _field(label, field, value, type, extra) {
            return '<label class="sme-field">' + esc(label) + '<input type="' + type + '" data-field="' + field + '" value="' + esc(value) + '" ' + (extra || "") + "></label>";
        }

        _applyField(o, field, raw) {
            const num = parseFloat(String(raw).replace(",", "."));
            this.commit(() => {
                if (field === "label") o.label = String(raw).trim().slice(0, 10);
                else if (field === "category") o.category = String(raw).trim().slice(0, 20);
                else if (field === "count" && isFinite(num)) this.setSeatCount(o, Math.max(1, num));
                else if (field === "diameter" && isFinite(num)) o.r = Math.max(20, Math.min(200, Math.round(num / 2)));
                else if ((field === "w" || field === "h") && isFinite(num)) o[field] = Math.max(30, Math.min(1200, Math.round(num)));
                else if (field === "spacing" && isFinite(num)) o.spacing = Math.max(40, Math.min(200, Math.round(num)));
                else if (field === "rotation" && isFinite(num)) {
                    o.rotation = ((Math.round(num) % 360) + 360) % 360;
                    o.manual_rotation = true; // keep a hand-set direction when the table moves
                } else if (field === "arrangement") {
                    o.arrangement = raw === "facing" ? "facing" : "around";
                    delete o.manual_rotation;
                    o.rotation = o.arrangement === "facing" ? M.rotationTowardsStage(o, this.room) : 0;
                }
                else if (field === "start" && isFinite(num)) {
                    o.start = Math.max(0, Math.round(num));
                    this.relabelSeats(o);
                } else if (field === "numbering") {
                    o.numbering = raw === "rtl" ? "rtl" : "ltr";
                    this.relabelSeats(o);
                }
            });
        }

        // ---- preview & modal ----------------------------------------------------------

        openModal(html) {
            this.modal.querySelector(".sme-modal-box").innerHTML = html;
            this.modal.hidden = false;
            const close = () => {
                this.modal.hidden = true;
                this.modal.querySelector(".sme-modal-box").innerHTML = "";
            };
            this.modal.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", close));
            this.modal.onkeydown = (e) => {
                if (e.key === "Escape") close();
            };
            const first = this.modal.querySelector("input, button");
            if (first) first.focus();
            return close;
        }

        preview() {
            this.openModal('<div class="sme-modal-head"><b>Podgląd jak kupujący</b> <span class="sme-muted">— kliknij miejsca lub stoliki, nic nie jest zapisywane</span>' +
                '<button type="button" data-close>Zamknij</button></div><div class="sme-preview-host"></div><p class="sme-preview-sum sme-muted">Wybrano: 0</p>');
            const states = {};
            M.expandSeats(this.layout).forEach((s) => {
                states[s.key] = "free";
            });
            const sum = this.modal.querySelector(".sme-preview-sum");
            const zonePrices = {};
            (this.layout.zones || []).forEach((z) => {
                zonePrices[z.code] = { price: String(z.percent), percent: z.percent };
            });
            new M.SeatMap(this.modal.querySelector(".sme-preview-host"), {
                mode: "sell", room: this.room, layout: clone(this.layout), states, orientation: this.map.orientation,
                price: "100", currency: "% ceny", zonePrices,
                onSelectionChange: (seats) => {
                    sum.textContent = "Wybrano: " + seats.length + (seats.length ? " — " + seats.map((s) => s.label).join(", ") : "");
                },
                onMessage: (m) => {
                    sum.textContent = m;
                },
            });
        }

        askName(title, initial) {
            return new Promise((resolve) => {
                const close = this.openModal('<form class="sme-name-form"><div class="sme-modal-head"><b>' + esc(title) + "</b></div>" +
                    '<label class="sme-field">Nazwa układu<input name="name" maxlength="200" required value="' + esc(initial) + '"></label>' +
                    '<div class="sme-modal-actions"><button type="button" data-close>Anuluj</button><button type="submit" class="sme-primary">Zapisz</button></div></form>');
                const form = this.modal.querySelector("form");
                form.addEventListener("submit", (e) => {
                    e.preventDefault();
                    const name = form.elements.name.value.trim();
                    close();
                    resolve(name || null);
                });
                this.modal.querySelectorAll("[data-close]").forEach((b) => b.addEventListener("click", () => resolve(null)));
            });
        }

        // In-page dialog (not window.confirm — works even with pop-ups blocked):
        // the layout is used by upcoming events whose seat maps will change too.
        confirmPropagation(events) {
            return new Promise((resolve) => {
                const fmt = (iso) => {
                    const d = new Date(iso);
                    const p = (n) => String(n).padStart(2, "0");
                    return p(d.getDate()) + "." + p(d.getMonth() + 1) + "." + String(d.getFullYear()).slice(2) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
                };
                const blocked = events.filter((e) => e.conflicts.length);
                const rows = events.map((e) => {
                    const change = [];
                    if (e.added) change.push("+" + e.added + " miejsc");
                    if (e.removed) change.push("−" + e.removed + " miejsc" + (e.blocked_removed ? " (w tym " + e.blocked_removed + " zablokowanych)" : ""));
                    const what = e.conflicts.length
                        ? '<span class="sme-error">NIE zostanie zmienione — zniknęłyby sprzedane/zarezerwowane miejsca: ' + esc(e.conflicts.slice(0, 8).join(", ")) + (e.conflicts.length > 8 ? "…" : "") + "</span>"
                        : (change.length ? change.join(", ") : "bez zmian w miejscach (etykiety, strefy, rysunek)") +
                          " · " + e.seats_before + " → " + e.seats_after + " miejsc" + (e.sold ? " · sprzedane miejsca (" + e.sold + ") zostają" : "");
                    return "<li><b>" + esc(e.title) + "</b> <span class=\"sme-muted\">" + fmt(e.start) + "</span><br>" + what + "</li>";
                }).join("");
                const close = this.openModal('<div class="sme-modal-head"><b>Ten układ jest używany na nadchodzących wydarzeniach</b></div>' +
                    "<p>Zapis zmieni plan sali na tych wydarzeniach:</p><ul class=\"sme-confirm-list\">" + rows + "</ul>" +
                    (blocked.length ? '<p class="sme-muted">Wydarzenia oznaczone na czerwono zostaną bez zmian — tam zmień układ na planie wydarzenia (przenieś miejsca) albo zapisz jako nowy układ.</p>' : "") +
                    '<div class="sme-modal-actions"><button type="button" data-cancel>Anuluj — nic nie zapisuj</button>' +
                    '<button type="button" class="sme-primary" data-ok>Zapisz i zmień plan na wydarzeniach</button></div>');
                const done = (value) => {
                    close();
                    resolve(value);
                };
                this.modal.querySelector("[data-ok]").addEventListener("click", () => done(true));
                this.modal.querySelector("[data-cancel]").addEventListener("click", () => done(false));
                this.modal.onkeydown = (e) => {
                    if (e.key === "Escape") done(false);
                };
            });
        }

        // ---- saving ----------------------------------------------------------------------

        async post(url, body) {
            const response = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
                body: JSON.stringify(body),
                credentials: "same-origin",
            });
            let data = {};
            try {
                data = await response.json();
            } catch (e) {
                data = { ok: false, errors: [{ message: "Błąd serwera (" + response.status + ")." }] };
            }
            return data;
        }

        payload() {
            return { schema_version: 1, grid_cm: this.layout.grid_cm, zones: this.layout.zones || [], objects: this.layout.objects };
        }
    }

    window.UchoSeatEditor = Editor;
})();
