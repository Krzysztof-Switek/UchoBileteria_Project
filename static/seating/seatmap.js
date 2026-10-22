/*
 * UCHO seat map — one SVG renderer for the layout editor, the buyer's map
 * and the staff map of an event.
 *
 * Coordinates: centimetres in the room's canonical plan (u right, v down,
 * stage at the top). The view can be turned so the stage is on the left
 * (better fit for a desktop screen); only the drawing turns, data never does.
 * Seat geometry mirrors apps/seating/layout.py::seat_positions — keep in step.
 *
 * Numbers arrive as JSON (json_script / fetch), never through Django {{ }}.
 */
(function () {
    "use strict";

    const NS = "http://www.w3.org/2000/svg";
    const CHAIR_R = 22;
    const CHAIR_OFFSET = 30;
    const PAD = 80;

    // ---- geometry (twin of layout.py) -------------------------------------

    function rotate(x, y, deg) {
        const a = (deg * Math.PI) / 180;
        return [x * Math.cos(a) - y * Math.sin(a), x * Math.sin(a) + y * Math.cos(a)];
    }

    function seatPositions(obj) {
        const cx = +obj.x || 0;
        const cy = +obj.y || 0;
        const rot = +obj.rotation || 0;
        const seats = obj.seats || [];
        let local = [];
        if (obj.type === "table_round") {
            const n = seats.length;
            const radius = (+obj.r || 45) + CHAIR_OFFSET;
            if (obj.arrangement === "facing") {
                // audience-side arc (twin of layout.py): seat 1 on the guests' left
                const step = facingStep(n);
                for (let j = 0; j < n; j++) {
                    const a = ((180 + ((n - 1) / 2 - j) * step) * Math.PI) / 180;
                    local.push([Math.sin(a) * radius, -Math.cos(a) * radius]);
                }
            } else {
                for (let i = 0; i < n; i++) {
                    const a = (2 * Math.PI * i) / Math.max(n, 1);
                    local.push([Math.sin(a) * radius, -Math.cos(a) * radius]);
                }
            }
        } else if (obj.type === "table_rect") {
            const w = +obj.w || 160;
            const h = +obj.h || 80;
            const s = obj.sides || {};
            let k = +s.top || 0;
            for (let j = 0; j < k; j++) local.push([-w / 2 + (w * (j + 0.5)) / k, -h / 2 - CHAIR_OFFSET]);
            k = +s.right || 0;
            for (let j = 0; j < k; j++) local.push([w / 2 + CHAIR_OFFSET, -h / 2 + (h * (j + 0.5)) / k]);
            k = +s.bottom || 0;
            for (let j = 0; j < k; j++) local.push([w / 2 - (w * (j + 0.5)) / k, h / 2 + CHAIR_OFFSET]);
            k = +s.left || 0;
            for (let j = 0; j < k; j++) local.push([-w / 2 - CHAIR_OFFSET, h / 2 - (h * (j + 0.5)) / k]);
        } else if (obj.type === "row") {
            const n = seats.length;
            const spacing = +obj.spacing || 55;
            for (let j = 0; j < n; j++) local.push([(j - (n - 1) / 2) * spacing, 0]);
        }
        return local.map(([lx, ly]) => {
            const [rx, ry] = rotate(lx, ly, rot);
            return [cx + rx, cy + ry];
        });
    }

    function facingStep(n) {
        return n <= 1 ? 45 : Math.min(45, 270 / (n - 1));
    }

    function facingFits(r, n) {
        if (n <= 1) return true;
        const radius = r + CHAIR_OFFSET;
        const minStep = (2 * Math.asin(Math.min(1, (CHAIR_R + 2) / radius)) * 180) / Math.PI;
        return facingStep(n) >= minStep;
    }

    /** Rotation that turns a "facing" table's chairs towards the stage. */
    function rotationTowardsStage(obj, room) {
        const stage = (room.obstacles || []).find((o) => o.kind === "stage" && o.points);
        if (!stage) return 0;
        const us = stage.points.map((p) => p[0]);
        const vs = stage.points.map((p) => p[1]);
        // aim at the middle of the stage, a little behind its front edge
        const su = (Math.min(...us) + Math.max(...us)) / 2;
        const sv = Math.max(...vs) - 100;
        const deg = (Math.atan2(su - obj.x, -(sv - obj.y)) * 180) / Math.PI;
        return Math.round(((deg % 360) + 360) % 360);
    }

    function objectPolygon(obj) {
        if (obj.type !== "table_rect" && obj.type !== "zone") return null;
        const w = +obj.w || 0;
        const h = +obj.h || 0;
        return [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]].map(([lx, ly]) => {
            const [rx, ry] = rotate(lx, ly, +obj.rotation || 0);
            return [(+obj.x || 0) + rx, (+obj.y || 0) + ry];
        });
    }

    function pointInPolygon(x, y, poly) {
        let inside = false;
        for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
            const [xi, yi] = poly[i];
            const [xj, yj] = poly[j];
            if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi || 1e-9) + xi) inside = !inside;
        }
        return inside;
    }

    function distPointSegment(px, py, ax, ay, bx, by) {
        const dx = bx - ax;
        const dy = by - ay;
        const len2 = dx * dx + dy * dy;
        const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
        return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
    }

    function circleHitsPolygon(x, y, r, poly) {
        if (pointInPolygon(x, y, poly)) return true;
        for (let i = 0; i < poly.length; i++) {
            const a = poly[i];
            const b = poly[(i + 1) % poly.length];
            if (distPointSegment(x, y, a[0], a[1], b[0], b[1]) < r) return true;
        }
        return false;
    }

    function circleHitsShape(x, y, r, shape) {
        if (shape.shape === "circle") return Math.hypot(x - shape.cx, y - shape.cy) < r + shape.r;
        return circleHitsPolygon(x, y, r, shape.points);
    }

    function isSellable(obj) {
        return obj.type === "table_round" || obj.type === "table_rect" || obj.type === "row";
    }

    function groupName(obj) {
        if (obj.type === "row") return "Rząd " + obj.label;
        if (obj.type === "zone") return "Strefa " + obj.label;
        return "Stolik " + obj.label;
    }

    function seatLabel(obj, seat) {
        return groupName(obj) + " · miejsce " + seat.label;
    }

    /** Every seat of the layout with its position and group info. */
    function expandSeats(layout) {
        const out = [];
        (layout.objects || []).forEach((obj) => {
            if (!isSellable(obj)) return;
            const pos = seatPositions(obj);
            obj.seats.forEach((seat, i) => {
                out.push({
                    key: seat.id, obj, seat, x: pos[i][0], y: pos[i][1],
                    label: seatLabel(obj, seat), disabled: !!seat.disabled,
                    sellMode: obj.sell_mode || "SEATS",
                });
            });
        });
        return out;
    }

    /** Bounding box of an object (with chairs), canonical coordinates. */
    function objectBounds(obj) {
        const pts = [];
        seatPositions(obj).forEach(([x, y]) => {
            pts.push([x - CHAIR_R, y - CHAIR_R], [x + CHAIR_R, y + CHAIR_R]);
        });
        if (obj.type === "table_round") {
            const r = +obj.r || 45;
            pts.push([obj.x - r, obj.y - r], [obj.x + r, obj.y + r]);
        }
        (objectPolygon(obj) || []).forEach((p) => pts.push(p));
        if (!pts.length) pts.push([obj.x - 20, obj.y - 20], [obj.x + 20, obj.y + 20]);
        const xs = pts.map((p) => p[0]);
        const ys = pts.map((p) => p[1]);
        return { x1: Math.min(...xs), y1: Math.min(...ys), x2: Math.max(...xs), y2: Math.max(...ys) };
    }

    // ---- DOM helpers -----------------------------------------------------

    function el(tag, attrs, parent) {
        const node = document.createElementNS(NS, tag);
        Object.entries(attrs || {}).forEach(([k, v]) => {
            if (v !== undefined && v !== null) node.setAttribute(k, v);
        });
        if (parent) parent.appendChild(node);
        return node;
    }

    function pointsAttr(points) {
        return points.map((p) => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" ");
    }

    function fmtMoney(value, currency) {
        return Number(value).toFixed(2).replace(".", ",") + " " + (currency || "");
    }

    // ---- the map ------------------------------------------------------------

    const STATE_TEXT = {
        free: "wolne", taken: "zajęte", blocked: "niedostępne", mine: "Twoje (w zamówieniu)",
        AVAILABLE: "wolne", HELD: "zarezerwowane — czeka na płatność", SOLD: "sprzedane",
        BLOCKED: "zablokowane",
    };

    class SeatMap {
        /**
         * opts: mode ("edit" | "sell" | "admin" | "preview"), room, layout,
         * states ({seat_key: state}), price, currency, orientation ("left" | "top"),
         * onSelectionChange(keys), onSeatActivate(seatInfo, event), onRender().
         */
        constructor(container, opts) {
            this.container = container;
            this.opts = Object.assign({ mode: "sell", states: {}, orientation: null }, opts);
            this.room = this.opts.room || {};
            this.layout = this.opts.layout || { objects: [] };
            this.states = this.opts.states || {};
            this.selected = new Set();
            this.highlight = new Set();
            this.orientation = this.opts.orientation || SeatMap.savedOrientation();
            this.seatNodes = new Map();
            this.objectNodes = new Map();
            this._build();
            this.render();
            this.fit();
        }

        static savedOrientation() {
            try {
                return localStorage.getItem("ucho-seatmap-orientation") || "left";
            } catch (e) {
                return "left";
            }
        }

        _build() {
            this.container.classList.add("sm-root", "sm-mode-" + this.opts.mode);
            this.wrap = document.createElement("div");
            this.wrap.className = "sm-canvas";
            this.container.appendChild(this.wrap);
            this.svg = el("svg", {
                class: "sm-svg", role: "group",
                "aria-label": this.opts.mode === "edit" ? "Plan sali — edytor" : "Plan sali",
                preserveAspectRatio: "xMidYMid meet",
            }, this.wrap);
            const defs = el("defs", {}, this.svg);
            const hatch = el("pattern", {
                id: "sm-hatch-" + (SeatMap._n = (SeatMap._n || 0) + 1),
                width: 24, height: 24, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)",
            }, defs);
            el("line", { x1: 0, y1: 0, x2: 0, y2: 24, class: "sm-hatch-line" }, hatch);
            this.hatchId = hatch.getAttribute("id");
            this.world = el("g", { class: "sm-world" }, this.svg);
            this.roomLayer = el("g", { class: "sm-room" }, this.world);
            this.gridLayer = el("g", { class: "sm-grid" }, this.world);
            this.objLayer = el("g", { class: "sm-objects" }, this.world);
            this.overlay = el("g", { class: "sm-overlay" }, this.world);

            this.tooltip = document.createElement("div");
            this.tooltip.className = "sm-tooltip";
            this.tooltip.hidden = true;
            this.wrap.appendChild(this.tooltip);

            const tools = document.createElement("div");
            tools.className = "sm-zoom";
            tools.innerHTML =
                '<button type="button" data-zoom="in" title="Powiększ (kółko myszy)">+</button>' +
                '<button type="button" data-zoom="out" title="Pomniejsz">−</button>' +
                '<button type="button" data-zoom="fit" title="Cała sala">⤢</button>' +
                '<button type="button" data-zoom="turn" title="Obróć widok (scena z lewej / u góry)">⟲</button>';
            this.wrap.appendChild(tools);
            tools.addEventListener("click", (e) => {
                const b = e.target.closest("button");
                if (!b) return;
                const what = b.dataset.zoom;
                if (what === "in") this.zoomBy(1.3);
                else if (what === "out") this.zoomBy(1 / 1.3);
                else if (what === "fit") this.fit();
                else if (what === "turn") this.setOrientation(this.orientation === "left" ? "top" : "left");
            });

            this.svg.addEventListener("wheel", (e) => {
                e.preventDefault();
                this.zoomBy(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX, e.clientY);
            }, { passive: false });

            if (this.opts.mode !== "edit") this._bindPan();
            this._bindSeatEvents();
        }

        // -- view -------------------------------------------------------------

        get W() {
            return +this.room.width_cm || 1000;
        }

        get D() {
            return +this.room.depth_cm || 1000;
        }

        setOrientation(orientation) {
            this.orientation = orientation;
            try {
                localStorage.setItem("ucho-seatmap-orientation", orientation);
            } catch (e) { /* private window — fine */ }
            this.render();
            this.fit();
        }

        /** Canonical -> screen-ish (the root SVG's user space). */
        project(u, v) {
            return this.orientation === "left" ? [v, this.W - u] : [u, v];
        }

        _worldTransform() {
            return this.orientation === "left" ? "matrix(0,-1,1,0,0," + this.W + ")" : "";
        }

        /** Keep text upright whatever the view orientation. */
        upright(u, v) {
            return this.orientation === "left" ? "rotate(90 " + u.toFixed(1) + " " + v.toFixed(1) + ")" : null;
        }

        fit() {
            const w = this.orientation === "left" ? this.D : this.W;
            const h = this.orientation === "left" ? this.W : this.D;
            this.view = { x: -PAD, y: -PAD, w: w + 2 * PAD, h: h + 2 * PAD };
            // Canvas follows the plan's proportions (no empty bands above/below),
            // within sensible limits; the editor keeps its fixed, tall canvas.
            if (this.opts.mode !== "edit" && this.opts.mode !== "preview") {
                const width = this.wrap.clientWidth || 800;
                const ideal = width * (this.view.h / this.view.w);
                const max = Math.max(320, window.innerHeight * 0.75);
                this.wrap.style.height = Math.round(Math.max(300, Math.min(max, ideal))) + "px";
            }
            this._applyView();
        }

        _applyView() {
            const v = this.view;
            this.svg.setAttribute("viewBox", [v.x, v.y, v.w, v.h].map((n) => n.toFixed(1)).join(" "));
            this.svg.classList.toggle("sm-zoomed", v.w < (this.orientation === "left" ? this.D : this.W) * 0.7);
        }

        zoomBy(factor, clientX, clientY) {
            const v = this.view;
            const rect = this.svg.getBoundingClientRect();
            const cx = clientX === undefined ? rect.left + rect.width / 2 : clientX;
            const cy = clientY === undefined ? rect.top + rect.height / 2 : clientY;
            const p = this.clientToRoot(cx, cy);
            const minW = 300;
            const maxW = (Math.max(this.W, this.D) + 2 * PAD) * 1.6;
            const newW = Math.max(minW, Math.min(maxW, v.w / factor));
            const k = newW / v.w;
            this.view = { x: p.x - (p.x - v.x) * k, y: p.y - (p.y - v.y) * k, w: newW, h: v.h * k };
            this._applyView();
        }

        clientToRoot(clientX, clientY) {
            const pt = this.svg.createSVGPoint();
            pt.x = clientX;
            pt.y = clientY;
            return pt.matrixTransform(this.svg.getScreenCTM().inverse());
        }

        /** Mouse position -> canonical plan coordinates (cm). */
        clientToWorld(clientX, clientY) {
            const pt = this.svg.createSVGPoint();
            pt.x = clientX;
            pt.y = clientY;
            const p = pt.matrixTransform(this.world.getScreenCTM().inverse());
            return { x: p.x, y: p.y };
        }

        /** Centre of what is visible now, canonical coordinates. */
        viewCenterWorld() {
            const rect = this.svg.getBoundingClientRect();
            return this.clientToWorld(rect.left + rect.width / 2, rect.top + rect.height / 2);
        }

        pixelsPerUnit() {
            const m = this.svg.getScreenCTM();
            return m ? Math.hypot(m.a, m.b) : 1;
        }

        _bindPan() {
            let start = null;
            this.svg.addEventListener("pointerdown", (e) => {
                if (e.button !== 0 || e.target.closest(".sm-seat, .sm-table-hit")) return;
                start = { x: e.clientX, y: e.clientY, view: Object.assign({}, this.view), moved: false };
                this.svg.setPointerCapture(e.pointerId);
            });
            this.svg.addEventListener("pointermove", (e) => {
                if (!start) return;
                const k = this.pixelsPerUnit();
                const dx = (e.clientX - start.x) / k;
                const dy = (e.clientY - start.y) / k;
                if (Math.abs(dx) + Math.abs(dy) > 2) start.moved = true;
                this.view = Object.assign({}, start.view, { x: start.view.x - dx, y: start.view.y - dy });
                this._applyView();
            });
            const end = () => {
                start = null;
            };
            this.svg.addEventListener("pointerup", end);
            this.svg.addEventListener("pointercancel", end);
        }

        // -- drawing ------------------------------------------------------------

        render() {
            this.world.setAttribute("transform", this._worldTransform());
            this._drawRoom();
            this._drawObjects();
            if (this.opts.onRender) this.opts.onRender(this);
        }

        _drawRoom() {
            const g = this.roomLayer;
            g.textContent = "";
            const room = this.room;
            // "room" mode (Ustawienia sali): everything is clickable, hidden
            // elements are still drawn — faded — so they can be brought back.
            const hidden = this.opts.hiddenIds || new Set();
            const groupFor = (item) => {
                const grp = el("g", { class: "sm-room-el" + (hidden.has(item.id) ? " is-room-hidden" : ""), "data-room-el": item.id }, g);
                el("title", {}, grp).textContent = item.name + (hidden.has(item.id) ? " (ukryty)" : "");
                return grp;
            };
            const bboxCenter = (points) => {
                const xs = points.map((p) => p[0]);
                const ys = points.map((p) => p[1]);
                return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2, Math.max(...xs)];
            };
            if (room.outline) {
                el("polygon", { points: pointsAttr(room.outline), class: "sm-floor" }, g);
            }
            (room.context || []).forEach((c) => {
                const grp = groupFor(c);
                if (c.shape === "polyline") {
                    el("polyline", { points: pointsAttr(c.points), class: "sm-ctx sm-ctx-" + c.kind }, grp);
                    return;
                }
                el("polygon", { points: pointsAttr(c.points), class: "sm-ctx sm-ctx-" + c.kind }, grp);
                const [cx, cy, maxU] = bboxCenter(c.points);
                if (c.kind === "overhead") {
                    // built-in: along the north-wall edge (keeps off the tables); drawn by hand: centred
                    this._text(grp, c.custom ? cx : maxU - 30, cy, c.name + " (nad głową)", "sm-ctx-label");
                } else if (c.kind === "area") {
                    this._text(grp, cx, cy, c.name, "sm-area-label");
                }
            });
            (room.obstacles || []).forEach((o) => {
                const grp = groupFor(o);
                const cls = "sm-obstacle sm-ob-" + o.kind;
                if (o.shape === "circle") {
                    el("circle", { cx: o.cx, cy: o.cy, r: Math.max(o.r, 7.5), class: cls }, grp);
                    return;
                }
                el("polygon", { points: pointsAttr(o.points), class: cls }, grp);
                if (o.kind === "stairs" || o.kind === "platform") {
                    el("polygon", { points: pointsAttr(o.points), fill: "url(#" + this.hatchId + ")", class: "sm-hatch" }, grp);
                }
                const [cx, cy] = bboxCenter(o.points);
                if (o.kind === "stage" || o.kind === "bar") {
                    this._text(grp, cx, cy, o.kind === "stage" ? "SCENA" : "BAR", "sm-big-label");
                } else if (o.kind === "custom") {
                    this._text(grp, cx, cy, o.name, "sm-area-label");
                }
            });
            (room.entrances || []).forEach((en) => {
                const grp = groupFor(en);
                el("polygon", { points: pointsAttr(en.points), class: "sm-entrance" }, grp);
                // label just inside the hall, next to the opening
                const us = en.points.map((p) => p[0]);
                const vs = en.points.map((p) => p[1]);
                this._text(grp, Math.max(...us) + 70, (Math.min(...vs) + Math.max(...vs)) / 2, en.name, "sm-entrance-label");
            });
        }

        _text(parent, u, v, text, cls) {
            const t = el("text", {
                x: u.toFixed(1), y: v.toFixed(1), class: cls, "text-anchor": "middle",
                "dominant-baseline": "central", transform: this.upright(u, v),
            }, parent);
            t.textContent = text;
            return t;
        }

        _drawObjects() {
            const g = this.objLayer;
            g.textContent = "";
            this.seatNodes.clear();
            this.objectNodes.clear();
            const edit = this.opts.mode === "edit";
            (this.layout.objects || []).forEach((obj) => {
                const zi = this.zoneIndex(obj.category);
                const og = el("g", {
                    class: "sm-object sm-type-" + obj.type + (zi >= 0 ? " sm-zoned sm-z" + zi : ""),
                    "data-object": obj.id,
                }, g);
                this.objectNodes.set(obj.id, og);
                const rot = +obj.rotation || 0;
                const mode = obj.sell_mode || "SEATS";
                if (obj.type === "table_round") {
                    el("circle", { cx: obj.x, cy: obj.y, r: obj.r, class: "sm-table sm-table-hit", "data-object": obj.id }, og);
                } else if (obj.type === "table_rect" || obj.type === "zone") {
                    el("rect", {
                        x: obj.x - obj.w / 2, y: obj.y - obj.h / 2, width: obj.w, height: obj.h, rx: obj.type === "zone" ? 0 : 6,
                        class: (obj.type === "zone" ? "sm-zone" : "sm-table") + " sm-table-hit",
                        transform: rot ? "rotate(" + rot + " " + obj.x + " " + obj.y + ")" : null,
                        "data-object": obj.id,
                    }, og);
                } else if (obj.type === "row" && edit) {
                    const pos = seatPositions(obj);
                    if (pos.length) {
                        el("line", {
                            x1: pos[0][0], y1: pos[0][1], x2: pos[pos.length - 1][0], y2: pos[pos.length - 1][1],
                            class: "sm-row-line sm-table-hit", "data-object": obj.id,
                        }, og);
                    }
                }
                // group label
                if (obj.type === "row") {
                    const pos = seatPositions(obj);
                    if (pos.length) {
                        const [dx, dy] = rotate(-(+obj.spacing || 55) * 0.85, 0, rot);
                        this._text(og, pos[0][0] + dx, pos[0][1] + dy, obj.label, "sm-row-label");
                    }
                } else {
                    const label = obj.type === "zone" ? obj.label : obj.label;
                    const t = this._text(og, +obj.x, +obj.y, label, obj.type === "zone" ? "sm-zone-label" : "sm-table-label");
                    t.setAttribute("data-object", obj.id);
                    if (obj.type !== "zone") t.classList.add("sm-table-hit");
                    if (!edit && obj.type !== "zone" && mode !== "SEATS") {
                        const sub = this._text(og, +obj.x, +obj.y + (this.orientation === "left" ? 0 : 26),
                            mode === "TABLE" ? "cały stolik" : "", "sm-table-sub");
                        if (this.orientation === "left") {
                            sub.setAttribute("x", (+obj.x - 26).toFixed(1));
                            sub.setAttribute("y", (+obj.y).toFixed(1));
                            sub.setAttribute("transform", this.upright(+obj.x - 26, +obj.y));
                        }
                    }
                }
                if (!isSellable(obj)) return;
                const pos = seatPositions(obj);
                obj.seats.forEach((seat, i) => {
                    if (seat.disabled && !edit) return;
                    const [x, y] = pos[i];
                    const sg = el("g", {
                        class: "sm-seat", "data-seat": seat.id, "data-object": obj.id,
                        tabindex: edit || this.opts.mode === "preview" ? null : "0",
                        role: edit ? null : "button",
                    }, og);
                    el("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: CHAIR_R, class: "sm-chair" }, sg);
                    this._text(sg, x, y, seat.label, "sm-seat-label");
                    if (seat.disabled) {
                        sg.classList.add("is-disabled");
                        el("line", { x1: x - 14, y1: y - 14, x2: x + 14, y2: y + 14, class: "sm-x" }, sg);
                    }
                    this.seatNodes.set(seat.id, { node: sg, obj, seat, x, y, label: seatLabel(obj, seat) });
                });
            });
            this.applyStates();
        }

        /** Editor grid: lines every `step` cm over the hall (null = hide).
         * Every metre line is a bit stronger so 0,5 m reads as half-steps. */
        drawGrid(step) {
            const g = this.gridLayer;
            g.textContent = "";
            if (!step) return;
            const pts = this.room.outline || [[0, 0], [this.W, this.D]];
            const us = pts.map((p) => p[0]);
            const vs = pts.map((p) => p[1]);
            const u1 = Math.floor(Math.min(...us) / step) * step;
            const u2 = Math.max(...us);
            const v1 = Math.floor(Math.min(...vs) / step) * step;
            const v2 = Math.max(...vs);
            const cls = (n) => "sm-grid-line" + (n % 100 === 0 && step < 100 ? " is-metre" : "");
            for (let u = u1; u <= u2 + 0.1; u += step) {
                el("line", { x1: u, y1: v1, x2: u, y2: v2, class: cls(u) }, g);
            }
            for (let v = v1; v <= v2 + 0.1; v += step) {
                el("line", { x1: u1, y1: v, x2: u2, y2: v, class: cls(v) }, g);
            }
        }

        // -- states & selection --------------------------------------------------

        setStates(states) {
            this.states = states || {};
            // a selected seat someone else just took: drop it and tell the page
            const lost = [];
            this.selected.forEach((key) => {
                const s = this.stateOf(key);
                if (s !== "free" && s !== "mine" && s !== "AVAILABLE") lost.push(key);
            });
            if (this.opts.mode === "sell" && lost.length) {
                lost.forEach((k) => this.selected.delete(k));
                if (this.opts.onLost) this.opts.onLost(lost.map((k) => this.seatNodes.get(k)).filter(Boolean));
                this._changed();
            }
            this.applyStates();
        }

        stateOf(key) {
            const s = this.states[key];
            if (s && typeof s === "object") return s.status;
            if (this.opts.mode === "edit") return "edit";
            return s || (this.opts.mode === "preview" ? "free" : "blocked");
        }

        applyStates() {
            this.seatNodes.forEach((info, key) => {
                const node = info.node;
                const state = this.stateOf(key);
                node.setAttribute("class", "sm-seat sm-state-" + state +
                    (this.selected.has(key) ? " is-selected" : "") +
                    (this.highlight.has(key) ? " is-highlight" : "") +
                    (info.seat.disabled ? " is-disabled" : ""));
                if (this.opts.mode !== "edit") {
                    node.setAttribute("aria-label", info.label + " — " + (this.selected.has(key) ? "wybrane" : STATE_TEXT[state] || state));
                    node.setAttribute("aria-pressed", this.selected.has(key) ? "true" : "false");
                }
            });
        }

        setSelected(keys) {
            this.selected = new Set(keys);
            this.applyStates();
        }

        setHighlight(keys) {
            this.highlight = new Set(keys);
            this.applyStates();
        }

        selectedSeats() {
            return [...this.selected].map((k) => this.seatNodes.get(k)).filter(Boolean)
                .sort((a, b) => this._order(a) - this._order(b));
        }

        _order(info) {
            const objects = this.layout.objects;
            return objects.indexOf(info.obj) * 1000 + info.obj.seats.indexOf(info.seat);
        }

        _changed() {
            this.applyStates();
            if (this.opts.onSelectionChange) this.opts.onSelectionChange(this.selectedSeats());
        }

        groupSeats(objId) {
            return [...this.seatNodes.values()].filter((i) => i.obj.id === objId);
        }

        _isFree(key) {
            const s = this.stateOf(key);
            return s === "free" || s === "mine";
        }

        /** Buyer clicked a seat or a table body. */
        _sellToggle(objId, seatKey) {
            const seats = this.groupSeats(objId);
            if (!seats.length) return;
            const obj = seats[0].obj;
            const mode = obj.sell_mode || "SEATS";
            const wholeTable = mode === "TABLE" || (mode === "BOTH" && !seatKey);
            if (obj.type === "row" && !seatKey) return;
            if (wholeTable) {
                const keys = seats.map((s) => s.key || s.seat.id);
                const allSelected = keys.every((k) => this.selected.has(k));
                if (allSelected) {
                    keys.forEach((k) => this.selected.delete(k));
                } else if (keys.every((k) => this._isFree(k))) {
                    keys.forEach((k) => this.selected.add(k));
                } else {
                    this._flash(seats[0].node, mode === "TABLE"
                        ? groupName(obj) + " sprzedawany jest tylko w całości, a część miejsc jest już zajęta."
                        : "Nie cały " + groupName(obj).toLowerCase() + " jest wolny — wybierz pojedyncze miejsca.");
                    return;
                }
            } else {
                if (this.selected.has(seatKey)) this.selected.delete(seatKey);
                else if (this._isFree(seatKey)) this.selected.add(seatKey);
                else return;
            }
            this._changed();
        }

        _flash(anchorNode, message) {
            if (this.opts.onMessage) this.opts.onMessage(message);
            else this._showTooltip(anchorNode, message);
        }

        _bindSeatEvents() {
            const mode = this.opts.mode;
            if (mode === "edit") return;
            let downAt = null;
            this.svg.addEventListener("pointerdown", (e) => {
                downAt = { x: e.clientX, y: e.clientY };
            });
            this.svg.addEventListener("click", (e) => {
                if (downAt && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 5) return; // a pan
                const seatNode = e.target.closest(".sm-seat");
                const tableNode = e.target.closest(".sm-table-hit");
                if (mode === "sell") {
                    if (seatNode) this._sellToggle(seatNode.dataset.object, seatNode.dataset.seat);
                    else if (tableNode) this._sellToggle(tableNode.dataset.object, null);
                } else if (mode === "admin" && this.opts.onSeatActivate) {
                    if (seatNode) this.opts.onSeatActivate(this.seatNodes.get(seatNode.dataset.seat), e);
                    else if (tableNode) this.opts.onTableActivate && this.opts.onTableActivate(tableNode.dataset.object, e);
                }
            });
            this.svg.addEventListener("keydown", (e) => {
                const seatNode = e.target.closest && e.target.closest(".sm-seat");
                if (!seatNode || (e.key !== "Enter" && e.key !== " ")) return;
                e.preventDefault();
                if (mode === "sell") this._sellToggle(seatNode.dataset.object, seatNode.dataset.seat);
                else if (mode === "admin" && this.opts.onSeatActivate) {
                    this.opts.onSeatActivate(this.seatNodes.get(seatNode.dataset.seat), e);
                }
            });
            this.svg.addEventListener("pointerover", (e) => {
                const seatNode = e.target.closest(".sm-seat");
                if (!seatNode) {
                    this.tooltip.hidden = true;
                    return;
                }
                const info = this.seatNodes.get(seatNode.dataset.seat);
                if (!info) return;
                this._showTooltip(seatNode, this.describe(info));
            });
            this.svg.addEventListener("pointerleave", () => {
                this.tooltip.hidden = true;
            });
            this.svg.addEventListener("focusin", (e) => {
                const seatNode = e.target.closest && e.target.closest(".sm-seat");
                if (seatNode) this._showTooltip(seatNode, this.describe(this.seatNodes.get(seatNode.dataset.seat)));
            });
        }

        /** Index of a price zone (colour sm-z0…sm-z5), -1 when none. */
        zoneIndex(code) {
            const zones = this.layout.zones || [];
            return code ? zones.findIndex((z) => z.code === code) : -1;
        }

        zoneOf(info) {
            const zones = this.layout.zones || [];
            return zones.find((z) => z.code === info.obj.category) || null;
        }

        /** Price of a seat: zone price from the page config, else the base price. */
        seatPrice(info) {
            const zp = this.opts.zonePrices || {};
            const zone = info.obj.category && zp[info.obj.category];
            if (zone) return parseFloat(zone.price);
            return this.opts.price !== undefined && this.opts.price !== null ? parseFloat(this.opts.price) : null;
        }

        describe(info) {
            const raw = this.states[info.seat.id];
            const state = this.stateOf(info.seat.id);
            let text = info.label;
            const zone = this.zoneOf(info);
            if (zone) text += " · strefa " + zone.code + (zone.name ? " (" + zone.name + ")" : "");
            const price = this.seatPrice(info);
            if (price !== null) text += " · " + fmtMoney(price, this.opts.currency);
            text += " · " + (this.selected.has(info.seat.id) ? "wybrane" : STATE_TEXT[state] || state);
            const mode = info.obj.sell_mode || "SEATS";
            if (this.opts.mode === "sell" && mode === "TABLE") text += " · tylko cały stolik";
            if (this.opts.mode === "sell" && mode === "BOTH") text += " · kliknij numer stolika, by wybrać cały";
            if (raw && typeof raw === "object") {
                if (raw.email) text += " · " + raw.email;
                if (raw.note) text += " · " + raw.note;
            }
            return text;
        }

        _showTooltip(anchor, text) {
            const box = anchor.getBoundingClientRect();
            const host = this.wrap.getBoundingClientRect();
            this.tooltip.textContent = text;
            this.tooltip.hidden = false;
            const left = Math.min(Math.max(box.left - host.left + box.width / 2, 90), host.width - 90);
            this.tooltip.style.left = left + "px";
            this.tooltip.style.top = (box.top - host.top - 8) + "px";
        }
    }

    // price-zone colours: same order as .sm-z0….sm-z5 in seatmap.css
    const ZONE_COLORS = ["#10b981", "#3b82f6", "#f59e0b", "#e11d48", "#8b5cf6", "#06b6d4"];

    window.UchoSeatMap = {
        ZONE_COLORS,
        SeatMap, seatPositions, objectPolygon, facingFits, rotationTowardsStage, objectBounds, expandSeats, groupName, seatLabel,
        isSellable, pointInPolygon, circleHitsPolygon, circleHitsShape, rotate, el, pointsAttr,
        fmtMoney, CHAIR_R, CHAIR_OFFSET,
    };
})();
