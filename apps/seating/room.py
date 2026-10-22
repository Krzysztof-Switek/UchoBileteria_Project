"""
Owner's adjustments of the hall drawing ("Ustawienia sali" in the panel).

`Room.base_geometry` is what `import_hall` produced from the technical model;
`Room.overrides` is what the owner changed by clicking on the plan:
    {"hidden": ["VEN_COLUMN_BAR_4", ...],            # elements not drawn
     "custom": [{"id": "c1", "name": "Realizator",   # elements drawn by hand
                 "kind": "overhead" | "obstacle" | "area",
                 "points": [[u, v], ...]}]}
`Room.geometry` (read by every map, editor and validator) = base + overrides.
Overrides survive a re-import, so the technical model can keep being refreshed.
"""

import copy
import math
import re

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.auditlog.services import log_action

CUSTOM_KINDS = {
    "overhead": "Nad głową (linia przerywana, nie przeszkadza stolikom)",
    "obstacle": "Przeszkoda na podłodze (stoliki nie mogą na niej stać)",
    "area": "Strefa z opisem (np. miejsce dla obsługi)",
}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_CUSTOM = 50


def element_ids(geometry: dict) -> set[str]:
    ids = set()
    for key in ("obstacles", "entrances", "context"):
        ids.update(e["id"] for e in geometry.get(key, []) if e.get("id"))
    return ids


def apply_overrides(base: dict, overrides: dict | None) -> dict:
    """Effective geometry: base minus hidden elements plus custom ones."""
    geometry = copy.deepcopy(base)
    overrides = overrides or {}
    hidden = set(overrides.get("hidden", []))
    for key in ("obstacles", "entrances", "context"):
        geometry[key] = [e for e in geometry.get(key, []) if e.get("id") not in hidden]
    for item in overrides.get("custom", []):
        element = {"id": item["id"], "name": item["name"], "shape": "polygon",
                   "points": item["points"], "custom": True}
        if item["kind"] == "obstacle":
            geometry.setdefault("obstacles", []).append(
                {**element, "kind": "custom", "blocking": True})
        else:
            geometry.setdefault("context", []).append({**element, "kind": item["kind"]})
    return geometry


def clean_overrides(data, base: dict) -> dict:
    """Validate what the room editor sent. Raises ValidationError."""
    if not isinstance(data, dict):
        raise ValidationError("Nieprawidłowe dane.")
    known = element_ids(base)
    hidden = data.get("hidden", [])
    if not isinstance(hidden, list) or any(not isinstance(h, str) for h in hidden):
        raise ValidationError("Nieprawidłowa lista ukrytych elementów.")
    hidden = sorted({h for h in hidden if h in known})

    custom = data.get("custom", [])
    if not isinstance(custom, list) or len(custom) > MAX_CUSTOM:
        raise ValidationError(f"Najwyżej {MAX_CUSTOM} własnych elementów.")
    cleaned, seen = [], set()
    for item in custom:
        if not isinstance(item, dict):
            raise ValidationError("Nieprawidłowy element.")
        cid = item.get("id")
        if not isinstance(cid, str) or not ID_RE.match(cid) or cid in seen or cid in known:
            raise ValidationError("Nieprawidłowe ID własnego elementu.")
        seen.add(cid)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            raise ValidationError("Każdy własny element musi mieć nazwę (do 60 znaków).")
        if item.get("kind") not in CUSTOM_KINDS:
            raise ValidationError(f"Nieznany rodzaj elementu „{name}”.")
        points = item.get("points")
        if not isinstance(points, list) or not 3 <= len(points) <= 50:
            raise ValidationError(f"„{name}”: kształt musi mieć od 3 do 50 punktów.")
        clean_points = []
        for p in points:
            if (not isinstance(p, list) or len(p) != 2
                    or not all(isinstance(c, (int, float)) and math.isfinite(c) for c in p)
                    or not all(-1000 <= c <= 5000 for c in p)):
                raise ValidationError(f"„{name}”: nieprawidłowy punkt.")
            clean_points.append([round(p[0], 1), round(p[1], 1)])
        cleaned.append({"id": cid, "name": name.strip(), "kind": item["kind"],
                        "points": clean_points})
    return {"hidden": hidden, "custom": cleaned}


def save_overrides(room, data, actor=None, refresh_event_maps: bool = False) -> int:
    """Store the owner's changes, rebuild Room.geometry; optionally redraw the hall
    on seat maps of upcoming events. Returns how many maps were refreshed."""
    room.overrides = clean_overrides(data, room.base_geometry)
    room.geometry = apply_overrides(room.base_geometry, room.overrides)
    room.save(update_fields=["overrides", "geometry", "updated_at"])
    log_action("seating.room_adjusted", room, actor=actor, metadata={
        "hidden": len(room.overrides["hidden"]), "custom": len(room.overrides["custom"]),
    })
    return refresh_maps(room) if refresh_event_maps else 0


def refresh_maps(room) -> int:
    """Redraw the hall on seat maps of upcoming events (seats/sales untouched)."""
    from .models import EventSeatMap

    snapshot = {"name": room.name, "width_cm": room.width_cm, "depth_cm": room.depth_cm,
                **room.geometry}
    maps = EventSeatMap.objects.filter(
        source_layout__room=room, event__start_at__gte=timezone.now()
    ).exclude(event__status__in=["CANCELLED", "FINISHED"])
    return maps.update(room_snapshot=snapshot)
