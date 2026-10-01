#!/usr/bin/env python3
"""Buduje pełny kontekst projektu Bileteria UCHO na start nowej sesji Claude.

Uruchamiaj przed każdą nową sesją:

    python build_context.py            # szybko (bez pytest)
    python build_context.py --full     # dodatkowo uruchamia pytest (~1-2 min)

Wynik trafia do `project_context/`:
- `CONTEXT.md`    — pierwszy plik sesji: zasady pracy, spis pamięci, stan gita
                    i środowiska, architektura (modele, URL-e), dokumenty, TODO
                    i mapa "zadanie → pliki".
- `MEMORY.md`     — drugi plik sesji: pełna treść pamięci Claude (decyzje,
                    preferencje użytkownika, ustalenia z poprzednich sesji).
- `CODE_INDEX.md` — indeks funkcji z docstringami, czytany na żądanie.
- `context.json`  — dane introspekcji Django w formie maszynowej.

Pliki są rozdzielone, bo każdy musi zmieścić się w jednym odczycie narzędzia
Read (~25k tokenów).

Skrypt nigdy nie czyta wartości z `.env` (tylko nazwy zmiennych z `.env.example`).
Introspekcja Django (modele, URL-e, migracje, tryb płatności) działa przez
interpreter z `.venv`; gdy się nie uda, sekcje wracają do analizy statycznej (AST).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "project_context"
APPS_DIR = ROOT / "apps"
DOCS_DIR = ROOT / "docs"
TESTS_DIR = ROOT / "tests"
TEMPLATES_DIR = ROOT / "templates"
VENV_PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

# Pamięć Claude Code dla tego projektu (slug = ścieżka z ":", "\" i "_" zamienionymi na "-").
CLAUDE_DIR = Path.home() / ".claude"
MEMORY_DIR = CLAUDE_DIR / "projects" / re.sub(r"[:\\/_]", "-", str(ROOT)) / "memory"
PLANS_DIR = CLAUDE_DIR / "plans"

SKIP_DIRS = {"__pycache__", "migrations", ".venv", "node_modules", ".git", "project_context"}
# Trafia do osobnego CODE_INDEX.md, więc nie obciąża CONTEXT.md.
CODE_FILES_FOR_INDEX = ("services.py", "views.py", "payments.py", "refunds.py", "sending.py",
                        "calendar.py", "roles.py", "statemachine.py", "admin_badges.py")
RECENT_DAYS = 21
MAX_COMMITS = 12
MAX_TODO_HITS = 40

TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b[:\s]?(.*)")

# Stałe zasady pracy — źródło: pamięć Claude + ustalenia z poprzednich sesji.
WORK_RULES = """\
- **Na starcie sesji uruchom dev server** (bez pytania), jeśli nie działa:
  `.venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000` w tle; wcześniej upewnij się,
  że istnieje `static/dist/tailwind.css` (inaczej `npm install && npm run build:css`).
- **Nigdy nie commituj** (`git add`/`git commit`) — commity robi użytkownik. Na koniec zgłoś:
  co zmienione, wynik `pytest` i `ruff check .`, że gotowe do commitu.
- **Nie edytuj plików UTF-8 PowerShellem** (`Get-Content`/`Set-Content` psują polskie znaki) —
  używaj Edit/Write albo Pythona.
- **Desktop-first** (90% użycia), wyjątek: skaner/odprawa (`base_terminal.html`, `apps/checkin`).
- **Motyw:** dark-first navy + akcent blue; nowe komponenty czytają zmienne CSS (`var(--accent)`),
  bez hardkodowanych hexów (poza kolorami stanów). Tokeny: `static/src/input.css`,
  admin: `static/admin_theme.css`. Po zmianie klas w szablonach: `npm run build:css`.
- **Grafikę/SVG/kolorowe przyciski weryfikuj na żywo w przeglądarce**, nie tylko testami
  (wcześniej złapane bugi: przecinek w SVG z polskiej lokalizacji, `a:link` w adminie).
- **Nie zmyślaj danych klubu/treści prawnych** (adres, telefon, regulamin) — to robi klub.
- Przy UX zaczynaj od `docs/Hendouts TODOs/21.08_audyt_ux_PLAN_TO_DO.md` (status ✅/🟡/⬜),
  odhaczaj tam ukończone pozycje.
- **Nawigacja:** każdy ekran personelu ma „Wstecz” + „Start” + ścieżkę. Nowy szablon na
  `base_app`/`base_terminal` musi definiować `{% block crumbs %}`; dane finansowe/biletowe
  pokazuj per wydarzenie (`PerEventChangelistMixin`), zbiorcze listy tylko na życzenie.
- **Panel = kafelki**, nie sekcje Django; daty w listach `dd.mm.rr gg:mm` (`apps/admin_format.py`).
- Panel = rozbudowa Django admin (nie osobny UI). Kierunek: zastąpić Interticket.
  Google Calendar zostaje jednokierunkowy (DB → Calendar). Hosting: VPS + Docker Compose.
"""

# Mapa "zadanie → od czego zacząć". Ścieżki nieistniejące są oznaczane przy generowaniu.
TASK_MAP = [
    ("Zakup biletu / zamówienia / rezerwacja pojemności",
     ["apps/orders/services.py", "apps/orders/views.py", "apps/orders/models.py",
      "templates/orders/", "tests/test_orders.py"]),
    ("Płatności (DEMO / Stripe), webhooki",
     ["apps/orders/payments.py", "apps/orders/providers/", "tests/test_demo_payments.py",
      "tests/test_stripe.py"]),
    ("Zwroty", ["apps/orders/refunds.py", "tests/test_refunds_reports.py"]),
    ("Wydarzenia, pule biletowe, publikacja",
     ["apps/events/models.py", "apps/events/services.py", "apps/events/admin.py",
      "tests/test_pool_logic.py"]),
    ("Google Calendar", ["apps/events/calendar.py", "tests/test_calendar.py"]),
    ("Bilety, QR, e-maile, PDF",
     ["apps/tickets/services.py", "apps/tickets/sending.py", "templates/emails/",
      "tests/test_tickets.py"]),
    ("Skaner / odprawa / tryb offline (mobile!)",
     ["apps/checkin/views.py", "apps/checkin/services.py", "templates/checkin/",
      "templates/base_terminal.html", "tests/test_checkin.py"]),
    ("Raporty, pulpit, eksporty CSV/XLSX, wyszukiwarka",
     ["apps/reports/services.py", "apps/reports/views.py", "templates/reports/",
      "tests/test_refunds_reports.py", "tests/test_search.py"]),
    ("Django admin (wygląd, kreator, odznaki)",
     ["apps/events/admin.py", "apps/orders/admin.py", "apps/admin_badges.py",
      "templates/admin/", "static/admin_theme.css", "tests/test_admin_ui.py"]),
    ("Role, logowanie, uprawnienia",
     ["apps/accounts/roles.py", "apps/accounts/auth_backends.py", "tests/test_roles.py",
      "tests/test_accounts_auth.py"]),
    ("Wygląd stron publicznych / motyw",
     ["static/src/input.css", "templates/base_public.html", "templates/base_app.html",
      "templates/partials/"]),
    ("Ustawienia, env, wdrożenie",
     ["config/settings.py", ".env.example", "compose.yml", "Dockerfile", "Caddyfile",
      "docs/RUNBOOK.md", "docs/PRODUCTION_CHECKLIST.md"]),
    ("Specyfikacja produktu", ["docs/Hendouts TODOs/TICKETING_SPEC.md"]),
]


# --------------------------------------------------------------------------- helpers

def run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return r.returncode, (r.stdout + r.stderr).strip()


def git(*args: str) -> str:
    code, out = run(["git", *args])
    return out if code == 0 else ""


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0].strip()


def iter_files(base: Path, exts: set[str]):
    for path in sorted(base.rglob("*")):
        if path.is_file() and path.suffix in exts and not (SKIP_DIRS & set(path.parts)):
            yield path


def strip_frontmatter(text: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line and not line.startswith(" "):
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip().strip('"')
            text = text[end + 4:]
    return meta, text.strip()


# --------------------------------------------------------------- django introspection

INTROSPECT_CODE = r"""
import json, os, sys
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, os.getcwd())
import django
django.setup()
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.urls import get_resolver, URLPattern, URLResolver

out = {"models": [], "urls": [], "admin": [], "settings": {}, "migrations": {}, "db": {}}

for m in apps.get_models():
    if not m.__module__.startswith("apps."):
        continue
    fields = []
    for f in m._meta.get_fields():
        if f.auto_created and not f.concrete:
            continue
        desc = f.name + ":" + f.get_internal_type()
        if f.is_relation and f.related_model:
            desc += "->" + f.related_model.__name__
        if getattr(f, "choices", None):
            more = "|…" if len(f.choices) > 8 else ""
            desc += "[" + "|".join(str(c[0]) for c in f.choices[:8]) + more + "]"
        if getattr(f, "null", False):
            desc += "?"
        fields.append(desc)
    out["models"].append({"app": m._meta.app_label, "name": m.__name__,
                          "verbose": str(m._meta.verbose_name), "fields": fields})

def walk(patterns, prefix="", ns=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            new_ns = ns + (p.namespace + ":" if p.namespace else "")
            if str(p.pattern).startswith("admin/"):
                out["urls"].append({"path": "/" + prefix + "admin/", "name": "admin:*",
                                    "view": "django.contrib.admin"})
                continue
            walk(p.url_patterns, prefix + str(p.pattern), new_ns)
        elif isinstance(p, URLPattern):
            cb = p.callback
            view = getattr(cb, "view_class", cb)
            out["urls"].append({"path": "/" + prefix + str(p.pattern),
                                "name": (ns + p.name) if p.name else "",
                                "view": view.__module__ + "." + getattr(view, "__qualname__", "?")})
walk(get_resolver().url_patterns)

for model, ma in admin.site._registry.items():
    out["admin"].append({"model": model._meta.label, "admin": type(ma).__name__,
                         "list_display": [str(x) for x in getattr(ma, "list_display", [])][:10],
                         "inlines": [i.__name__ for i in getattr(ma, "inlines", [])]})

db = settings.DATABASES["default"]
out["settings"] = {"DEBUG": settings.DEBUG, "DB_ENGINE": db["ENGINE"], "DB_NAME": str(db["NAME"]),
                   "TIME_ZONE": settings.TIME_ZONE, "LANGUAGE_CODE": settings.LANGUAGE_CODE,
                   "EMAIL_BACKEND": settings.EMAIL_BACKEND,
                   "STRIPE_CONFIGURED": bool(getattr(settings, "STRIPE_SECRET_KEY", "")),
                   "CALENDAR_CONFIGURED": bool(getattr(settings, "GOOGLE_CALENDAR_ID", ""))}

try:
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    ex = MigrationExecutor(connection)
    plan = ex.migration_plan(ex.loader.graph.leaf_nodes())
    out["migrations"] = {"unapplied": [f"{m.app_label}.{m.name}" for m, _ in plan]}
except Exception as e:
    out["migrations"] = {"error": str(e)}

try:
    counts = {}
    for m in apps.get_models():
        if m.__module__.startswith("apps."):
            counts[m.__name__] = m.objects.count()
    out["db"]["counts"] = counts
    try:
        from apps.orders.models import PaymentConfig
        cfg = PaymentConfig.objects.first()
        if cfg is not None:
            out["db"]["payment_config"] = {k: str(v) for k, v in cfg.__dict__.items()
                                           if not k.startswith("_") and "key" not in k.lower()
                                           and "secret" not in k.lower()}
    except Exception as e:
        out["db"]["payment_config_error"] = str(e)
except Exception as e:
    out["db"]["error"] = str(e)

print("@@JSON@@" + json.dumps(out, ensure_ascii=False))
"""


def django_introspect() -> dict | None:
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    code, out = run([python, "-c", INTROSPECT_CODE], timeout=120)
    marker = out.find("@@JSON@@")
    if code != 0 or marker == -1:
        print(f"[!] Introspekcja Django nie powiodła się — używam AST.\n{out[-800:]}",
              file=sys.stderr)
        return None
    return json.loads(out[marker + len("@@JSON@@"):].splitlines()[0])


# ------------------------------------------------------------------------- sections

def section_rules() -> str:
    return WORK_RULES


def section_environment(data: dict | None) -> str:
    lines = []
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=0.5):
            server = "✅ działa na http://127.0.0.1:8000/"
    except OSError:
        server = "⬜ nie działa — uruchom (patrz zasady)"
    lines.append(f"- Dev server: {server}")
    css = ROOT / "static" / "dist" / "tailwind.css"
    css_src = ROOT / "static" / "src" / "input.css"
    if not css.exists():
        lines.append("- `static/dist/tailwind.css`: ⬜ BRAK — `npm install && npm run build:css`")
    elif css_src.exists() and css_src.stat().st_mtime > css.stat().st_mtime:
        lines.append("- `static/dist/tailwind.css`: 🟡 starszy niż `input.css` — przebuduj CSS")
    else:
        lines.append("- `static/dist/tailwind.css`: ✅ zbudowany")
    lines.append(f"- `.venv`: {'✅' if VENV_PYTHON.exists() else '⬜ brak'}  "
                 f"`.env`: {'✅' if (ROOT / '.env').exists() else '⬜ brak'}  "
                 f"`db.sqlite3`: {'✅' if (ROOT / 'db.sqlite3').exists() else '⬜ brak'}")
    _, pyver = run([str(VENV_PYTHON), "--version"]) if VENV_PYTHON.exists() else (1, "?")
    lines.append(f"- Python (venv): {pyver}")
    if data:
        s = data["settings"]
        engine = s["DB_ENGINE"].rsplit(".", 1)[-1]
        lines.append(f"- Django settings: DEBUG={s['DEBUG']}, DB={engine}"
                     f" (`{Path(s['DB_NAME']).name}`), TZ={s['TIME_ZONE']}, "
                     f"LANG={s['LANGUAGE_CODE']}, e-mail={s['EMAIL_BACKEND'].rsplit('.', 1)[-1]}")
        lines.append(f"- Stripe skonfigurowany: {s['STRIPE_CONFIGURED']}, "
                     f"Google Calendar: {s['CALENDAR_CONFIGURED']}")
        mig = data["migrations"]
        if "error" in mig:
            lines.append(f"- Migracje: ⚠ {mig['error']}")
        elif mig["unapplied"]:
            lines.append(f"- Migracje: 🟡 niezaaplikowane: {', '.join(mig['unapplied'])}")
        else:
            lines.append("- Migracje: ✅ wszystkie zaaplikowane")
        db = data["db"]
        if "payment_config" in db:
            cfg = ", ".join(f"{k}={v}" for k, v in db["payment_config"].items() if k != "id")
            lines.append(f"- PaymentConfig (lokalna baza): {cfg}")
        if "counts" in db:
            nonzero = {k: v for k, v in db["counts"].items() if v}
            lines.append("- Rekordy w lokalnej bazie: "
                         + ", ".join(f"{k}={v}" for k, v in nonzero.items()))
    creds = ROOT / "dev_credentials.json"
    try:
        accounts = json.loads(read(creds)).get("accounts", []) if creds.exists() else []
    except ValueError:
        accounts = []
    if accounts:
        lines.append("- Konta demo (`dev_credentials.json`, przyciski na stronach logowania):"
                     + "".join(f"\n  - `{a['username']}` / `{a['password']}` — "
                               f"{a.get('label') or a.get('role')}" for a in accounts))
    else:
        lines.append("- Konta demo: brak `dev_credentials.json` — "
                     "`.venv/Scripts/python.exe manage.py load_dev_users`")
    env_vars = [ln.lstrip("# ").split("=", 1)[0] for ln in read(ROOT / ".env.example").splitlines()
                if "=" in ln and not ln.startswith("# Copy")]
    lines.append(f"- Zmienne środowiskowe (`.env.example`): {', '.join(env_vars)}")
    return "\n".join(lines)


def section_commands() -> str:
    lines = ["```bash",
             ".venv/Scripts/python.exe manage.py runserver 127.0.0.1:8000",
             ".venv/Scripts/python.exe -m pytest -q        # testy (config.settings_test)",
             ".venv/Scripts/python.exe -m ruff check .     # lint",
             "npm run build:css  |  npm run watch:css      # Tailwind -> static/dist/tailwind.css",
             ".venv/Scripts/python.exe manage.py seed_demo # demo: /wydarzenia/test-koncert/",
             ".venv/Scripts/python.exe manage.py load_dev_users  # konta demo",
             "```", "", "Management commands:"]
    for cmd in sorted(APPS_DIR.glob("*/management/commands/*.py")):
        if cmd.stem == "__init__":
            continue
        tree = ast.parse(read(cmd))
        help_txt = ast.get_docstring(tree) or ""
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "help" for t in node.targets):
                if isinstance(node.value, ast.Constant):
                    help_txt = str(node.value.value)
                elif isinstance(node.value, ast.JoinedStr | ast.BinOp):
                    help_txt = help_txt or ast.unparse(node.value)
        lines.append(f"- `{cmd.stem}` ({rel(cmd.parent.parent.parent)}) — "
                     f"{first_line(help_txt)[:160]}")
    return "\n".join(lines)


def section_git() -> str:
    branch = git("branch", "--show-current") or "?"
    status = git("status", "--porcelain")
    lines = [f"**Branch:** `{branch}`", ""]
    if status:
        rows = status.splitlines()
        lines.append(f"**Niezacommitowane zmiany ({len(rows)}):**")
        lines += [f"- `{r}`" for r in rows[:40]]
        # Tylko linia podsumowania — lista plików jest już wyżej.
        stat = git("diff", "--shortstat", "HEAD")
        if stat:
            lines += ["", f"_{stat.strip()}_"]
    else:
        lines.append("**Working tree czysty.**")
    lines += ["", f"**Ostatnie {MAX_COMMITS} commitów (z plikami):**"]
    log = git("log", f"-{MAX_COMMITS}", "--date=short", "--name-only",
              "--pretty=format:@@%h %ad %s")
    for i, block in enumerate(log.split("@@")[1:]):
        head, *files = [x for x in block.splitlines() if x.strip()]
        # Pliki tylko dla najnowszych commitów — starsze są tłem, nie bieżącą pracą.
        files = [f for f in files if "/migrations/" not in f] if i < 5 else []
        shown = ", ".join(files[:6]) + (f" … (+{len(files) - 6})" if len(files) > 6 else "")
        lines.append(f"- `{head}`" + (f"  \n  {shown}" if shown else ""))
    return "\n".join(lines)


def section_recent_files() -> str:
    cutoff = datetime.now() - timedelta(days=RECENT_DAYS)
    hits = []
    for base in (APPS_DIR, ROOT / "config", TEMPLATES_DIR, ROOT / "static", TESTS_DIR, DOCS_DIR):
        for p in iter_files(base, {".py", ".html", ".css", ".js", ".md"}):
            if "dist" in p.parts:
                continue
            hits.append((datetime.fromtimestamp(p.stat().st_mtime), p))
    hits.sort(reverse=True)
    recent = [h for h in hits if h[0] >= cutoff]
    note = ""
    if not recent:
        note = f"_Nic w ostatnich {RECENT_DAYS} dniach — ostatnio zmieniane pliki:_\n"
        recent = hits[:10]
    return note + "\n".join(f"- {mt:%Y-%m-%d %H:%M} `{rel(p)}`" for mt, p in recent[:30])


def memory_notes() -> list[tuple[str, str, str, str]]:
    """(name, type, description, body) of each memory note, self-reference skipped."""
    if not MEMORY_DIR.exists():
        return []
    notes = []
    for path in sorted(MEMORY_DIR.glob("*.md")):
        if path.name in ("MEMORY.md", "bileteria-ucho-build-context.md"):
            continue
        meta, body = strip_frontmatter(read(path))
        mtype = re.search(r"^\s*type:\s*(\w+)", read(path), re.M)  # not node_type
        notes.append((meta.get("name", path.stem), mtype.group(1) if mtype else "?",
                      meta.get("description", ""), body))
    return notes


def section_memory_index() -> str:
    """Short index for CONTEXT.md — full text lives in MEMORY.md."""
    notes = memory_notes()
    if not notes:
        return f"_Brak notatek w `{MEMORY_DIR}`._"
    lines = ["**Wczytaj `project_context/MEMORY.md` jako drugi plik sesji** — pełna treść "
             "decyzji i preferencji użytkownika (spis poniżej to tylko nagłówki).", ""]
    lines += [f"- **{name}** ({mtype}) — {desc}" for name, mtype, desc, _ in notes]
    return "\n".join(lines)


def memory_document() -> str:
    parts = ["# Pamięć Claude — Bileteria UCHO", "",
             "_Wygenerowano przez `build_context.py` z katalogu pamięci Claude Code. "
             "Drugi plik do wczytania na starcie sesji (po CONTEXT.md)._"]
    for name, mtype, desc, body in memory_notes():
        parts += ["", f"## {name} ({mtype})", f"_{desc}_", "", body]
    return "\n".join(parts) + "\n"


def section_models(data: dict | None) -> str:
    lines = []
    if data:
        by_app: dict[str, list[dict]] = {}
        for m in data["models"]:
            by_app.setdefault(m["app"], []).append(m)
        for app, models in sorted(by_app.items()):
            lines.append(f"**{app}**")
            for m in models:
                lines.append(f"- `{m['name']}` ({m['verbose']}): {', '.join(m['fields'])}")
        lines += ["", "_Legenda: `pole:Typ`, `->Model` relacja, `[a|b]` choices, `?` null._"]
    else:
        for models_py in sorted(APPS_DIR.glob("*/models.py")):
            tree = ast.parse(read(models_py))
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    fields = [t.id for n in node.body if isinstance(n, ast.Assign)
                              for t in n.targets if isinstance(t, ast.Name)]
                    lines.append(f"- `{rel(models_py)}` `{node.name}`: {', '.join(fields)}")
    return "\n".join(lines)


def section_urls(data: dict | None) -> str:
    if not data:
        return "_Introspekcja niedostępna — patrz `config/urls.py` i `apps/*/urls.py`._"
    lines = ["_Format: `URL` name — widok podany tylko, gdy nie wynika z `app.views.<name>`._", ""]
    for u in data["urls"]:
        view = u["view"].removeprefix("apps.")
        app, _, name = u["name"].partition(":")
        implied = {f"{app}.views.{name}", f"{app}.views.{app[:-1]}_{name}"} if name else set()
        suffix = "" if view in implied else f" — `{view}`"
        lines.append(f"- `{u['path']}` {u['name']}{suffix}")
    return "\n".join(lines)


def section_admin(data: dict | None) -> str:
    if not data:
        return "_Introspekcja niedostępna._"
    lines = []
    for a in data["admin"]:
        if a["model"].split(".")[0] not in {m["app"] for m in data["models"]}:
            continue
        extra = f"; inlines: {', '.join(a['inlines'])}" if a["inlines"] else ""
        lines.append(f"- `{a['model']}` → `{a['admin']}` "
                     f"(list_display: {', '.join(a['list_display'])}{extra})")
    return "\n".join(lines)


def section_code_index() -> str:
    """Spis publicznych funkcji/klas z najważniejszych modułów + pierwsza linia docstringu."""
    lines = []
    files = [p for p in iter_files(APPS_DIR, {".py"}) if p.name in CODE_FILES_FOR_INDEX]
    files += sorted((APPS_DIR / "orders" / "providers").glob("*.py"))
    for path in files:
        if path.name == "__init__.py":
            continue
        try:
            tree = ast.parse(read(path))
        except SyntaxError as exc:
            lines.append(f"**`{rel(path)}`** — ⚠ SyntaxError: {exc}")
            continue
        entries, bare = [], []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) \
                    and not node.name.startswith("_"):
                kind = "class " if isinstance(node, ast.ClassDef) else ""
                args = "" if kind else "(" + ", ".join(a.arg for a in node.args.args) + ")"
                doc = first_line(ast.get_docstring(node))
                if doc:
                    entries.append(f"  - {kind}`{node.name}{args}` — {doc[:70]}")
                else:
                    bare.append(f"`{kind}{node.name}`")
        if bare:
            entries.append(f"  - bez docstringu: {', '.join(bare)}")
        if entries:
            mod_doc = first_line(ast.get_docstring(tree))
            lines.append(f"**`{rel(path)}`**" + (f" — {mod_doc[:110]}" if mod_doc else ""))
            lines += entries
    return "\n".join(lines)


def section_templates() -> str:
    groups: dict[str, list[str]] = {}
    for p in iter_files(TEMPLATES_DIR, {".html", ".txt"}):
        r = p.relative_to(TEMPLATES_DIR)
        groups.setdefault(r.parts[0] if len(r.parts) > 1 else ".", []).append(
            "/".join(r.parts[1:]) if len(r.parts) > 1 else r.name)
    lines = [f"- `templates/{k + '/' if k != '.' else ''}`: {', '.join(v)}"
             for k, v in sorted(groups.items())]
    static = [rel(p) for p in iter_files(ROOT / "static", {".css", ".js"}) if "dist" not in p.parts]
    lines.append(f"- static (źródła): {', '.join(static)}")
    return "\n".join(lines)


def section_tests(run_pytest: bool) -> str:
    lines = []
    total = 0
    for p in sorted(TESTS_DIR.glob("test_*.py")):
        n = len(re.findall(r"^\s*(?:async\s+)?def test_", read(p), re.M))
        total += n
        doc = first_line(ast.get_docstring(ast.parse(read(p))))
        lines.append(f"- `{rel(p)}` ({n})" + (f" — {doc[:120]}" if doc else ""))
    lines.insert(0, f"Funkcji testowych (statycznie, bez parametryzacji): **{total}**\n")
    code, out = run([str(VENV_PYTHON), "-m", "ruff", "check", "."], timeout=120)
    lines += ["", f"**ruff check .:** {'✅ czysto' if code == 0 else '❌ ' + out.splitlines()[-1]}"]
    if run_pytest:
        code, out = run([str(VENV_PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                        timeout=900)
        summary = out.splitlines()[-1] if out else "?"
        lines.append(f"**pytest:** {'✅' if code == 0 else '❌'} {summary}")
        if code != 0:
            failed = [ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR"))]
            lines += [f"  - `{f}`" for f in failed[:20]]
    else:
        lines.append("**pytest:** nie uruchamiano (użyj `--full`)")
    return "\n".join(lines)


def section_docs() -> str:
    lines = []
    docs = [ROOT / "README.md", *sorted(DOCS_DIR.rglob("*.md"))]
    for p in docs:
        if not p.exists():
            continue
        text = read(p)
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")),
                     p.stem)
        h2 = [ln[3:].strip() for ln in text.splitlines() if ln.startswith("## ")]
        mt = datetime.fromtimestamp(p.stat().st_mtime)
        status = ""
        counts = {s: text.count(s) for s in ("✅", "🟡", "⬜")}
        if sum(counts.values()):
            status = " · status: " + " ".join(f"{k}{v}" for k, v in counts.items())
        lines.append(f"- **{title}** — `{rel(p)}` ({mt:%Y-%m-%d}, {len(text) // 1000} kB{status})")
        if h2:
            lines.append(f"  sekcje: {' / '.join(h2[:14])}" + (" / …" if len(h2) > 14 else ""))
    return "\n".join(lines)


def section_open_items() -> str:
    """Otwarte pozycje (🟡/⬜) i otwarte decyzje z planów w docs/Hendouts TODOs."""
    lines = []
    for p in sorted((DOCS_DIR / "Hendouts TODOs").glob("*.md")):
        open_rows = [ln.strip() for ln in read(p).splitlines()
                     if ("🟡" in ln or "⬜" in ln) and not ln.lstrip().startswith((">", "Legenda"))]
        open_rows = [r for r in open_rows if not re.fullmatch(r"[|\s\-:✅🟡⬜/]*", r)]
        if open_rows:
            lines.append(f"**`{rel(p)}`**")
            lines += [f"- {r[:200]}" for r in open_rows[:25]]
    return "\n".join(lines) if lines else "_Brak otwartych pozycji oznaczonych 🟡/⬜._"


def section_plans() -> str:
    if not PLANS_DIR.exists():
        return "_Brak katalogu planów._"
    lines = []
    for p in sorted(PLANS_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        text = read(p)
        if not re.search(r"\bUCHO\b|[Bb]ileteri", text):
            continue
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")),
                     p.stem)
        mt = datetime.fromtimestamp(p.stat().st_mtime)
        lines.append(f"- {mt:%Y-%m-%d} **{title[:120]}** — `{p}`")
    return "\n".join(lines[:15]) if lines else "_Brak planów dotyczących UCHO._"


def section_todos() -> str:
    hits = []
    for base in (APPS_DIR, ROOT / "config", TEMPLATES_DIR, ROOT / "static" / "src", TESTS_DIR):
        for p in iter_files(base, {".py", ".html", ".css", ".js"}):
            for no, line in enumerate(read(p).splitlines(), 1):
                m = TODO_RE.search(line)
                if m:
                    hits.append(f"- `{rel(p)}:{no}` — {m.group(0).strip()[:160]}")
    if not hits:
        return "_Brak TODO/FIXME w kodzie._"
    more = f"\n- … oraz {len(hits) - MAX_TODO_HITS} więcej" if len(hits) > MAX_TODO_HITS else ""
    return "\n".join(hits[:MAX_TODO_HITS]) + more


def section_task_map() -> str:
    lines = []
    for task, paths in TASK_MAP:
        shown = [f"`{p}`" + ("" if (ROOT / p).exists() else " ✗") for p in paths]
        lines.append(f"- **{task}:** {', '.join(shown)}")
    return "\n".join(lines)


def section_stack() -> str:
    reqs = [ln.split(";")[0].strip() for ln in read(ROOT / "requirements.txt").splitlines()
            if ln.strip() and not ln.startswith("#")]
    dev = [ln.strip() for ln in read(ROOT / "requirements-dev.txt").splitlines()
           if ln.strip() and not ln.startswith(("#", "-r"))]
    return "\n".join([
        "Django 6 + HTMX, Python 3.14, SQLite (dev) / Postgres 17 (prod), Tailwind 4 (lokalny"
        " build, bez CDN), Stripe za interfejsem `PaymentProvider`, tryb DEMO (demowaluta,"
        " `is_demo`).",
        f"- requirements.txt: {', '.join(reqs)}",
        f"- requirements-dev.txt: {', '.join(dev)}",
        "- Produkcja: `compose.yml` (app + Postgres + Caddy + cron co minutę: expire_orders,"
        " send_emails, sync_calendar, snapshot_emergency_lists), VPS. Docker NIE jest"
        " zainstalowany lokalnie.",
        "- Settings: `config/settings.py` (env przez django-environ), testy:"
        " `config/settings_test.py`.",
    ])


# ---------------------------------------------------------------------------- build

def build(run_pytest: bool) -> tuple[str, dict | None]:
    data = django_introspect()
    now = datetime.now()
    s = [
        "# Kontekst projektu — Bileteria UCHO",
        "",
        f"_Wygenerowano przez `build_context.py` — {now:%Y-%m-%d %H:%M}. "
        "Plik nadpisywany przy każdym uruchomieniu._",
        "",
        "> **Dla Claude:** to pierwszy plik do wczytania w sesji, drugi to `MEMORY.md` (pełna",
        "> pamięć z poprzednich sesji). Tu: zasady pracy, stan repo i środowiska, mapa",
        "> architektury (modele, URL-e) i mapa „zadanie → pliki”. Nie czytaj całego repo —",
        "> otwieraj tylko pliki wskazane dla bieżącego zadania (sekcja 3).",
        "",
        "## 1. Zasady pracy (obowiązkowe)", "", section_rules(),
        "## 2. Pamięć Claude z poprzednich sesji (spis)", "", section_memory_index(), "",
        "## 3. Zadanie → od czego zacząć", "", section_task_map(), "",
        "## 4. Środowisko lokalne (stan teraz)", "", section_environment(data), "",
        "## 5. Stack i infrastruktura", "", section_stack(), "",
        "## 6. Komendy", "", section_commands(), "",
        "## 7. Git", "", section_git(), "",
        f"## 8. Pliki zmienione w ostatnich {RECENT_DAYS} dniach (mtime)", "",
        section_recent_files(), "",
        "## 9. Otwarte pozycje z planów (🟡/⬜)", "", section_open_items(), "",
        "## 10. Dokumentacja", "", section_docs(), "",
        "## 11. Plany Claude dotyczące projektu (`~/.claude/plans`)", "", section_plans(), "",
        "## 12. Modele danych", "", section_models(data), "",
        "## 13. Mapa URL", "", section_urls(data), "",
        "## 14. Django admin — zarejestrowane modele", "", section_admin(data), "",
        "## 15. Indeks kodu", "",
        "Publiczne funkcje i klasy z modułów usług (z pierwszą linią docstringu) są w"
        " `project_context/CODE_INDEX.md` — otwórz, gdy szukasz, gdzie jest dana logika.", "",
        "## 16. Szablony i statyczne", "", section_templates(), "",
        "## 17. Testy i lint", "", section_tests(run_pytest), "",
        "## 18. TODO / FIXME w kodzie", "", section_todos(), "",
    ]
    return "\n".join(s), data


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Buduje project_context/CONTEXT.md")
    parser.add_argument("--full", action="store_true", help="uruchom też pytest")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    report, data = build(args.full)
    (OUTPUT_DIR / "CONTEXT.md").write_text(report, encoding="utf-8")
    # Osobne pliki, żeby każdy mieścił się w jednym odczycie narzędzia Read.
    memory = memory_document()
    (OUTPUT_DIR / "MEMORY.md").write_text(memory, encoding="utf-8")
    (OUTPUT_DIR / "CODE_INDEX.md").write_text(
        "# Indeks kodu — Bileteria UCHO\n\n" + section_code_index() + "\n", encoding="utf-8")
    if data:
        (OUTPUT_DIR / "context.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    # ~2.1 bajta/token dla polskiego markdownu; narzędzie Read czyta max ~25k tokenów naraz.
    for name, text in (("CONTEXT.md", report), ("MEMORY.md", memory)):
        size = len(text.encode("utf-8"))
        print(f"Zapisano: project_context/{name} ({size // 1000} kB, ~{size / 2100:.1f}k tokenów)")
        if size > 51_000:
            print(f"[!] {name} przekracza ~24k tokenów — może nie zmieścić się w jednym odczycie.")
    print("Na start sesji: wczytaj project_context/CONTEXT.md, potem project_context/MEMORY.md")


if __name__ == "__main__":
    main()
