"""
Layout document (SeatingLayout.data): schema validation, seat geometry and
expansion into sellable seats.

The browser editor (static/seating/seatmap.js) uses the same geometry rules —
keep `seat_positions` and its JS twin `seatPositions` in step.

Schema v1:
    {"schema_version": 1, "grid_cm": 100, "objects": [
        {"id": "t1", "type": "table_round", "label": "1", "x": 400, "y": 600,
         "r": 45, "rotation": 0, "sell_mode": "SEATS", "category": "",
         "seats": [{"id": "t1-s1", "label": "1", "disabled": false}, ...]},
        {"id": "t2", "type": "table_rect", ..., "w": 160, "h": 80,
         "sides": {"top": 2, "right": 1, "bottom": 2, "left": 1}, "seats": [...]},
        {"id": "rA", "type": "row", "label": "A", ..., "spacing": 55, "seats": [...]},
        {"id": "z1", "type": "zone", "label": "FOH", "x": .., "y": .., "w": .., "h": ..}
    ]}
Price zones (optional, a layout option): "zones": [{"code": "A", "name": "Przy scenie",
"percent": 120}, ...]; a table/row joins a zone through "category" = zone code.
A seat costs the active pool's price × percent (see `zone_price`); no zone = 100 %.
Round tables: "arrangement": "around" (default, chairs all round) or "facing" —
chairs only on the audience side, on an arc behind the table, so everybody
looks at the stage over it; "rotation" turns that arc (the editor aims it at the
stage), seats numbered left to right as the guests see the stage.
Seats of a rectangular table go clockwise from the top-left corner (top side
left→right, right side top→bottom, bottom right→left, left bottom→top); seats of
a round table clockwise from 12 o'clock (+rotation); a row left→right.
"""

import math
import re

CHAIR_R = 22  # chair drawn as a circle, cm
CHAIR_OFFSET = 30  # table edge -> chair centre, cm
SCHEMA_VERSION = 1
GRID_CHOICES = (50, 100, 200)  # editor grid: 0,5 m / 1 m / 2 m
DEFAULT_GRID_CM = 100
MAX_ZONES = 6
ZONE_CODE_RE = re.compile(r"^[A-Z0-9]{1,3}$")
ZONE_PERCENT = (10, 500)

TABLE_TYPES = ("table_round", "table_rect")
SELLABLE_TYPES = (*TABLE_TYPES, "row")
OBJECT_TYPES = (*SELLABLE_TYPES, "zone")
SELL_MODES = ("SEATS", "TABLE", "BOTH")
SIDES = ("top", "right", "bottom", "left")

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MAX_OBJECTS = 400
MAX_SEATS_TOTAL = 1500

FACING_STEP_MAX = 45  # degrees between chairs on the audience-side arc
FACING_ARC_MAX = 270

LIMITS = {
    "r": (20, 200),
    "w": (30, 1200),
    "h": (30, 1200),
    "spacing": (40, 200),
    "round_seats": (1, 16),
    "side_seats": (0, 12),
    "row_seats": (1, 60),
}


class LayoutError(ValueError):
    """The layout document is malformed (message lists the problems)."""

    def __init__(self, problems: list[dict]):
        self.problems = problems
        super().__init__("; ".join(p["message"] for p in problems))


# --- geometry ---------------------------------------------------------------


def _rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    a = math.radians(degrees)
    return x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)


def seat_positions(obj: dict) -> list[tuple[float, float]]:
    """Centre of every seat of an object (canonical plan, cm), in seat order."""
    kind = obj.get("type")
    cx, cy = float(obj.get("x", 0)), float(obj.get("y", 0))
    rotation = float(obj.get("rotation", 0) or 0)
    seats = obj.get("seats") or []
    local: list[tuple[float, float]] = []

    if kind == "table_round":
        n = len(seats)
        radius = float(obj.get("r", 45)) + CHAIR_OFFSET
        if obj.get("arrangement") == "facing":
            # arc centred behind the table (180° = away from a stage at the top),
            # seat 1 on the guests' left
            step = facing_step(n)
            for j in range(n):
                a = math.radians(180 + ((n - 1) / 2 - j) * step)
                local.append((math.sin(a) * radius, -math.cos(a) * radius))
        else:
            for i in range(n):
                a = math.radians(360 * i / max(n, 1))
                local.append((math.sin(a) * radius, -math.cos(a) * radius))
    elif kind == "table_rect":
        w, h = float(obj.get("w", 160)), float(obj.get("h", 80))
        sides = obj.get("sides") or {}
        k = int(sides.get("top", 0) or 0)
        local += [(-w / 2 + w * (j + 0.5) / k, -h / 2 - CHAIR_OFFSET) for j in range(k)]
        k = int(sides.get("right", 0) or 0)
        local += [(w / 2 + CHAIR_OFFSET, -h / 2 + h * (j + 0.5) / k) for j in range(k)]
        k = int(sides.get("bottom", 0) or 0)
        local += [(w / 2 - w * (j + 0.5) / k, h / 2 + CHAIR_OFFSET) for j in range(k)]
        k = int(sides.get("left", 0) or 0)
        local += [(-w / 2 - CHAIR_OFFSET, h / 2 - h * (j + 0.5) / k) for j in range(k)]
    elif kind == "row":
        n = len(seats)
        spacing = float(obj.get("spacing", 55))
        local = [((j - (n - 1) / 2) * spacing, 0.0) for j in range(n)]

    result = []
    for lx, ly in local:
        rx, ry = _rotate(lx, ly, rotation)
        result.append((cx + rx, cy + ry))
    return result


def facing_step(n: int) -> float:
    """Angle between neighbouring chairs on the audience-side arc."""
    return FACING_STEP_MAX if n <= 1 else min(FACING_STEP_MAX, FACING_ARC_MAX / (n - 1))


def rotation_towards_stage(x: float, y: float, room_geometry: dict) -> int:
    """Rotation that turns a "facing" table's chairs to the stage (twin of the JS
    rotationTowardsStage): aim at the middle of the stage, 1 m behind its front."""
    stage = next((o for o in room_geometry.get("obstacles", [])
                  if o.get("kind") == "stage" and o.get("points")), None)
    if stage is None:
        return 0
    us = [p[0] for p in stage["points"]]
    vs = [p[1] for p in stage["points"]]
    su, sv = (min(us) + max(us)) / 2, max(vs) - 100
    return round(math.degrees(math.atan2(su - x, -(sv - y)))) % 360


def facing_fits(r: float, n: int) -> bool:
    """Do n chairs fit on the arc without touching each other?"""
    if n <= 1:
        return True
    radius = r + CHAIR_OFFSET
    min_step = math.degrees(2 * math.asin(min(1.0, (CHAIR_R + 2) / radius)))
    return facing_step(n) >= min_step


def object_polygon(obj: dict) -> list[tuple[float, float]] | None:
    """Footprint of a rectangular object (table_rect / zone), rotated."""
    if obj.get("type") not in ("table_rect", "zone"):
        return None
    w, h = float(obj.get("w", 0)), float(obj.get("h", 0))
    cx, cy = float(obj.get("x", 0)), float(obj.get("y", 0))
    rotation = float(obj.get("rotation", 0) or 0)
    corners = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    out = []
    for lx, ly in corners:
        rx, ry = _rotate(lx, ly, rotation)
        out.append((cx + rx, cy + ry))
    return out


def point_in_polygon(x: float, y: float, polygon) -> bool:
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
            inside = not inside
        j = i
    return inside


def _dist_point_segment(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def circle_hits_polygon(x, y, r, polygon) -> bool:
    if point_in_polygon(x, y, polygon):
        return True
    n = len(polygon)
    return any(
        _dist_point_segment(x, y, *polygon[i], *polygon[(i + 1) % n]) < r for i in range(n)
    )


def _circle_hits_shape(x, y, r, shape: dict) -> bool:
    if shape.get("shape") == "circle":
        return math.hypot(x - shape["cx"], y - shape["cy"]) < r + shape["r"]
    return circle_hits_polygon(x, y, r, shape["points"])


# --- validation ---------------------------------------------------------------


def _num(obj, key, default=None):
    value = obj.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(key)
    if not math.isfinite(value):
        raise TypeError(key)
    return value


def object_name(obj: dict) -> str:
    kind = obj.get("type")
    label = obj.get("label") or obj.get("id")
    if kind in TABLE_TYPES:
        return f"Stolik {label}"
    if kind == "row":
        return f"Rząd {label}"
    return f"Strefa {label}"


def validate_layout(data, room_geometry: dict | None = None) -> tuple[list[dict], list[dict]]:
    """
    Return (errors, warnings); each item is {"object": id | None, "message": str}.
    Errors block saving; warnings (outside the hall, collisions, blocked exits)
    are shown to the manager, who may knowingly accept them.
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    def err(obj_id, message):
        errors.append({"object": obj_id, "message": message})

    if not isinstance(data, dict) or not isinstance(data.get("objects"), list):
        err(None, "Nieprawidłowy dokument układu (brak listy obiektów).")
        return errors, warnings
    objects = data["objects"]
    if len(objects) > MAX_OBJECTS:
        err(None, f"Za dużo obiektów (maks. {MAX_OBJECTS}).")
        return errors, warnings

    zone_codes = _validate_zones(data.get("zones", []), err)

    object_ids: set[str] = set()
    seat_ids: set[str] = set()
    labels: dict[str, set] = {"table": set(), "row": set()}
    total_seats = 0
    sellable = 0

    for obj in objects:
        if not isinstance(obj, dict):
            err(None, "Nieprawidłowy obiekt w układzie.")
            continue
        oid = obj.get("id")
        if not isinstance(oid, str) or not ID_RE.match(oid):
            err(None, "Obiekt ma nieprawidłowe ID.")
            continue
        if oid in object_ids:
            err(oid, f"Zdublowane ID obiektu „{oid}”.")
            continue
        object_ids.add(oid)
        kind = obj.get("type")
        if kind not in OBJECT_TYPES:
            err(oid, f"Nieznany typ obiektu „{kind}”.")
            continue
        name = object_name(obj)
        try:
            _num(obj, "x")
            _num(obj, "y")
            _num(obj, "rotation", 0)
            if kind == "table_round":
                r = _num(obj, "r")
                if not LIMITS["r"][0] <= r <= LIMITS["r"][1]:
                    err(oid, f"{name}: promień poza zakresem {LIMITS['r']} cm.")
            if kind in ("table_rect", "zone"):
                for key in ("w", "h"):
                    v = _num(obj, key)
                    if not LIMITS[key][0] <= v <= LIMITS[key][1]:
                        err(oid, f"{name}: wymiar poza zakresem {LIMITS[key]} cm.")
            if kind == "row":
                s = _num(obj, "spacing")
                if not LIMITS["spacing"][0] <= s <= LIMITS["spacing"][1]:
                    err(oid, f"{name}: rozstaw krzeseł poza zakresem {LIMITS['spacing']} cm.")
        except TypeError as exc:
            err(oid, f"{name}: brak lub błędna wartość liczbowa „{exc}”.")
            continue

        label = obj.get("label")
        if not isinstance(label, str) or not label.strip() or len(label) > 10:
            err(oid, f"{name}: numer/nazwa musi mieć od 1 do 10 znaków.")
            continue
        if kind == "zone":
            continue

        bucket = "row" if kind == "row" else "table"
        if label.strip().lower() in labels[bucket]:
            err(oid, f"Zdublowany numer: {name}.")
        labels[bucket].add(label.strip().lower())

        mode = obj.get("sell_mode", "SEATS")
        if mode not in SELL_MODES:
            err(oid, f"{name}: nieznany tryb sprzedaży.")
        if kind == "row" and mode != "SEATS":
            err(oid, f"{name}: rząd sprzedaje się tylko na pojedyncze miejsca.")
        category = obj.get("category", "")
        if not isinstance(category, str) or len(category) > 20:
            err(oid, f"{name}: nieprawidłowa kategoria.")
        elif zone_codes and category and category not in zone_codes:
            err(oid, f"{name}: nieznana strefa cenowa „{category}”.")
        elif zone_codes and not category:
            warnings.append({"object": oid,
                             "message": f"{name} nie ma strefy cenowej — cena podstawowa (100%)."})

        seats = obj.get("seats")
        if not isinstance(seats, list):
            err(oid, f"{name}: brak listy miejsc.")
            continue
        n = len(seats)
        if kind == "table_round" and not (
            LIMITS["round_seats"][0] <= n <= LIMITS["round_seats"][1]
        ):
            err(oid, f"{name}: stolik okrągły może mieć od 1 do 16 miejsc.")
        if kind == "table_round":
            arrangement = obj.get("arrangement", "around")
            if arrangement not in ("around", "facing"):
                err(oid, f"{name}: nieznany sposób ustawienia krzeseł.")
            elif arrangement == "facing" and not facing_fits(float(obj.get("r", 45)), n):
                err(oid, f"{name}: tyle krzeseł nie zmieści się od strony widowni — "
                         "zmniejsz liczbę miejsc albo powiększ stolik.")
        if kind == "row" and not LIMITS["row_seats"][0] <= n <= LIMITS["row_seats"][1]:
            err(oid, f"{name}: rząd może mieć od 1 do 60 miejsc.")
        if kind == "table_rect":
            sides = obj.get("sides")
            if not isinstance(sides, dict):
                err(oid, f"{name}: brak liczby miejsc na bokach.")
                continue
            try:
                counts = [int(sides.get(side, 0) or 0) for side in SIDES]
            except (TypeError, ValueError):
                err(oid, f"{name}: błędna liczba miejsc na boku.")
                continue
            if any(not 0 <= c <= LIMITS["side_seats"][1] for c in counts):
                err(oid, f"{name}: na jednym boku może być od 0 do 12 miejsc.")
            if sum(counts) != n:
                err(oid, f"{name}: liczba miejsc nie zgadza się z rozkładem na boki.")
            if n == 0:
                err(oid, f"{name}: stolik musi mieć co najmniej jedno miejsce.")

        seat_labels: set[str] = set()
        for seat in seats:
            sid = seat.get("id") if isinstance(seat, dict) else None
            if not isinstance(sid, str) or not ID_RE.match(sid):
                err(oid, f"{name}: miejsce z nieprawidłowym ID.")
                continue
            if sid in seat_ids:
                err(oid, f"{name}: zdublowane ID miejsca „{sid}”.")
            seat_ids.add(sid)
            slabel = seat.get("label")
            if not isinstance(slabel, str) or not slabel.strip() or len(slabel) > 6:
                err(oid, f"{name}: numer miejsca musi mieć od 1 do 6 znaków.")
                continue
            if slabel.strip().lower() in seat_labels:
                err(oid, f"{name}: zdublowany numer miejsca {slabel}.")
            seat_labels.add(slabel.strip().lower())
            total_seats += 1
            if not seat.get("disabled"):
                sellable += 1

    if total_seats > MAX_SEATS_TOTAL:
        err(None, f"Za dużo miejsc (maks. {MAX_SEATS_TOTAL}).")
    if not errors and sellable == 0:
        err(None, "Układ musi mieć co najmniej jedno miejsce do sprzedaży.")

    if not errors and room_geometry:
        warnings = warnings + layout_warnings(objects, room_geometry)
    return errors, warnings


def _validate_zones(zones, err) -> set[str]:
    """Check the price-zone list; returns the valid zone codes."""
    if zones in (None, []):
        return set()
    if not isinstance(zones, list) or len(zones) > MAX_ZONES:
        err(None, f"Strefy cenowe: najwyżej {MAX_ZONES} stref.")
        return set()
    codes: set[str] = set()
    for zone in zones:
        code = zone.get("code") if isinstance(zone, dict) else None
        if not isinstance(code, str) or not ZONE_CODE_RE.match(code):
            err(None, "Strefa cenowa: kod to 1–3 wielkie litery lub cyfry (np. A).")
            continue
        if code in codes:
            err(None, f"Zdublowany kod strefy cenowej „{code}”.")
        codes.add(code)
        name = zone.get("name", "")
        if not isinstance(name, str) or len(name) > 40:
            err(None, f"Strefa {code}: nazwa może mieć najwyżej 40 znaków.")
        percent = zone.get("percent")
        if (isinstance(percent, bool) or not isinstance(percent, int)
                or not ZONE_PERCENT[0] <= percent <= ZONE_PERCENT[1]):
            err(None, f"Strefa {code}: procent ceny musi być liczbą całkowitą "
                      f"{ZONE_PERCENT[0]}–{ZONE_PERCENT[1]}.")
    return codes


def zone_percents(data: dict) -> dict[str, int]:
    return {z["code"]: z["percent"] for z in data.get("zones", []) or []}


def zone_price(base, percent: int):
    """Price of a seat in a zone: base × percent. 100 % is the base exactly;
    other zones are rounded to whole złoty (prices people can read at a glance)."""
    from decimal import ROUND_HALF_UP, Decimal

    base = Decimal(base)
    if percent == 100:
        return base
    return (base * percent / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(
        Decimal("0.01"))


def layout_warnings(objects: list[dict], room: dict) -> list[dict]:
    """Physical problems: outside the hall, on an obstacle, blocking an exit,
    in the pit in front of the stage, chairs colliding with each other."""
    warnings: list[dict] = []
    outline = room.get("outline") or []
    obstacles = [o for o in room.get("obstacles", []) if o.get("blocking")]
    entrances = room.get("entrances", [])
    pit = room.get("pit")
    seats: list[tuple[str, float, float]] = []

    def warn(oid, message):
        if not any(w["object"] == oid and w["message"] == message for w in warnings):
            warnings.append({"object": oid, "message": message})

    for obj in objects:
        oid, name = obj["id"], object_name(obj)
        shapes = []
        if obj["type"] == "table_round":
            shapes.append(("circle", float(obj["x"]), float(obj["y"]), float(obj["r"])))
        polygon = object_polygon(obj)
        positions = seat_positions(obj)
        for x, y in positions:
            seats.append((oid, x, y))

        points_to_check = [(x, y, CHAIR_R) for x, y in positions]
        if shapes:
            _, x, y, r = shapes[0]
            points_to_check.append((x, y, r))
        if polygon:
            points_to_check += [(px, py, 1) for px, py in polygon]

        if outline and any(not point_in_polygon(x, y, outline) for x, y, _ in points_to_check):
            warn(oid, f"{name} wychodzi poza obrys sali.")
        for x, y, r in points_to_check:
            for obstacle in obstacles:
                if _circle_hits_shape(x, y, r, obstacle):
                    warn(oid, f"{name} koliduje z: {obstacle['name']}.")
        if polygon and obj["type"] == "table_rect":
            for obstacle in obstacles:
                if obstacle.get("shape") == "circle" and point_in_polygon(
                    obstacle["cx"], obstacle["cy"], polygon
                ):
                    warn(oid, f"{name} koliduje z: {obstacle['name']}.")
        for x, y in positions:
            for entrance in entrances:
                if circle_hits_polygon(x, y, CHAIR_R + entrance.get("clearance", 100),
                                       entrance["points"]):
                    warn(oid, f"{name} zastawia przejście: {entrance['name']}.")
            if pit and circle_hits_polygon(x, y, CHAIR_R, pit["points"]):
                warn(oid, f"{name} stoi w fosie przed sceną (za barierkami).")

    # chairs overlapping chairs of another object, or standing on a table
    tables = [o for o in objects if o["type"] in TABLE_TYPES]
    for i, (oid, x, y) in enumerate(seats):
        for oid2, x2, y2 in seats[i + 1:]:
            if oid2 != oid and math.hypot(x - x2, y - y2) < 2 * CHAIR_R - 4:
                warn(oid, f"Krzesła stolików/rzędów nachodzą na siebie ({oid} i {oid2}).")
        for table in tables:
            if table["id"] == oid:
                continue
            if table["type"] == "table_round":
                hit = math.hypot(x - table["x"], y - table["y"]) < table["r"] + CHAIR_R - 4
            else:
                hit = circle_hits_polygon(x, y, CHAIR_R - 4, object_polygon(table))
            if hit:
                warn(oid, f"{object_name_by_id(objects, oid)} nachodzi na "
                          f"{object_name(table)}.")
    return warnings


def object_name_by_id(objects, oid) -> str:
    for obj in objects:
        if obj["id"] == oid:
            return object_name(obj)
    return oid


def clean_layout(data) -> dict:
    """Validate and return a normalised copy (only known keys). Raises LayoutError."""
    errors, _ = validate_layout(data)
    if errors:
        raise LayoutError(errors)
    keep = {
        "table_round": ("id", "type", "label", "x", "y", "r", "rotation", "sell_mode",
                        "category", "arrangement", "manual_rotation", "seats"),
        "table_rect": ("id", "type", "label", "x", "y", "w", "h", "rotation", "sell_mode",
                       "category", "sides", "seats"),
        "row": ("id", "type", "label", "x", "y", "spacing", "rotation", "sell_mode",
                "category", "numbering", "start", "seats"),
        "zone": ("id", "type", "label", "x", "y", "w", "h", "rotation"),
    }
    objects = []
    for obj in data["objects"]:
        clean = {k: obj[k] for k in keep[obj["type"]] if k in obj}
        clean.setdefault("rotation", 0)
        if obj["type"] != "zone":
            clean.setdefault("sell_mode", "SEATS")
            clean.setdefault("category", "")
            clean["label"] = clean["label"].strip()
            clean["seats"] = [
                {"id": s["id"], "label": s["label"].strip(), "disabled": bool(s.get("disabled"))}
                for s in obj["seats"]
            ]
            if obj["type"] == "table_rect":
                clean["sides"] = {side: int(obj["sides"].get(side, 0) or 0) for side in SIDES}
            if obj["type"] == "table_round":
                clean["arrangement"] = (
                    "facing" if obj.get("arrangement") == "facing" else "around"
                )
                if obj.get("manual_rotation"):
                    clean["manual_rotation"] = True
                else:
                    clean.pop("manual_rotation", None)
            if obj["type"] == "row":
                clean["numbering"] = "rtl" if obj.get("numbering") == "rtl" else "ltr"
                start = obj.get("start", 1)
                clean["start"] = start if isinstance(start, int) and 0 <= start <= 999 else 1
        objects.append(clean)
    grid = data.get("grid_cm", DEFAULT_GRID_CM)
    zones = [
        {"code": z["code"], "name": (z.get("name") or "").strip(), "percent": z["percent"]}
        for z in data.get("zones", []) or []
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "grid_cm": grid if grid in GRID_CHOICES else DEFAULT_GRID_CM,
        "zones": zones,
        "objects": objects,
    }


def expand_seats(data: dict) -> list[dict]:
    """Every sellable (not disabled) seat, in drawing order — the rows of EventSeat."""
    result = []
    index = 0
    percents = zone_percents(data)
    for obj in data.get("objects", []):
        if obj.get("type") not in SELLABLE_TYPES:
            continue
        kind = "ROW" if obj["type"] == "row" else "TABLE"
        for seat in obj["seats"]:
            index += 1
            if seat.get("disabled"):
                continue
            result.append({
                "seat_key": seat["id"],
                "group_key": obj["id"],
                "group_kind": kind,
                "group_label": obj["label"],
                "seat_label": seat["label"],
                "sell_mode": obj.get("sell_mode", "SEATS"),
                "category": obj.get("category", ""),
                "price_percent": percents.get(obj.get("category", ""), 100),
                "sort_index": index,
            })
    return result


def layout_counts(data: dict) -> tuple[int, int]:
    """(sellable seats, tables + rows) for the library cards."""
    groups = [o for o in data.get("objects", []) if o.get("type") in SELLABLE_TYPES]
    return len(expand_seats(data)), len(groups)


def _label_list(labels: list[str], limit: int = 12) -> str:
    def key(text):
        return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", str(text))]

    labels = sorted(labels, key=key)
    return ", ".join(labels[:limit]) + ("…" if len(labels) > limit else "")


def layout_summary(data: dict) -> dict:
    """For the library cards (06.10.2026): tables and rows counted separately
    and the sale rules in plain words, e.g. 'Tylko całe stoliki: 1, 2, 3' +
    'pozostałe: miejsca albo cały stolik'."""
    groups = [o for o in data.get("objects", []) if o.get("type") in SELLABLE_TYPES]
    tables = [o for o in groups if o["type"] != "row"]
    rows = [o for o in groups if o["type"] == "row"]
    by_mode: dict[str, list[str]] = {}
    for obj in tables:
        by_mode.setdefault(obj.get("sell_mode", "SEATS"), []).append(obj.get("label", "?"))
    words = {"TABLE": "tylko całe stoliki", "BOTH": "miejsca albo cały stolik",
             "SEATS": "pojedyncze miejsca"}
    notes = []
    if len(by_mode) > 1:
        # name the smallest groups by label, the biggest one as "pozostałe"
        order = sorted(by_mode, key=lambda m: len(by_mode[m]))
        for mode in order[:-1]:
            notes.append(f"{words[mode].capitalize()}: {_label_list(by_mode[mode])}")
        notes.append(f"pozostałe stoliki: {words[order[-1]]}")
    elif by_mode:
        notes.append(f"Wszystkie stoliki: {words[next(iter(by_mode))]}")
    if rows:
        notes.append(f"Rzędy krzeseł: {_label_list([o.get('label', '?') for o in rows])}")
    zones = data.get("zones") or []
    if zones:
        notes.append("Strefy cenowe: " + " · ".join(
            f"{z['code']} {z.get('name', '')} {z['percent']}%".replace("  ", " ") for z in zones))
    return {"tables": len(tables), "rows": len(rows), "notes": notes}
