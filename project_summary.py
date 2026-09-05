#!/usr/bin/env python3
"""Generuje "spis treści" projektu do wczytania na starcie sesji.

Skanuje repo (git, dokumenty, aplikacje Django, testy, konfigurację) i
zapisuje jeden zwarty plik Markdown, który daje pełny kontekst bez
konieczności czytania całego kodu od zera.

Użycie:
    python project_summary.py [--no-archive]

Wynik trafia do `docs/project summaty/`:
- `LATEST.md`               — zawsze nadpisywany, to właśnie ten plik
                               warto wczytać na start sesji.
- `project_summary_*.md`    — oznaczona datą kopia archiwalna (chyba że
                               podano --no-archive).
"""

from __future__ import annotations

import argparse
import re
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
HANDOUTS_DIR = DOCS_DIR / "Hendouts TODOs"
OUTPUT_DIR = DOCS_DIR / "project summaty"
APPS_DIR = ROOT / "apps"
TESTS_DIR = ROOT / "tests"

KEY_DOCS = [
    ROOT / "README.md",
    DOCS_DIR / "RUNBOOK.md",
    DOCS_DIR / "PRODUCTION_CHECKLIST.md",
]

CONFIG_FILES = [
    ("pyproject.toml", "konfiguracja narzędzi (pytest, ruff)"),
    ("requirements.txt", "zależności produkcyjne"),
    ("requirements-dev.txt", "zależności deweloperskie"),
    ("compose.yml", "Docker Compose: app + Postgres + Caddy + cron"),
    ("Dockerfile", "obraz aplikacji"),
    ("Caddyfile", "reverse proxy / TLS"),
    (".env.example", "szablon zmiennych środowiskowych"),
]

SCAN_DIRS = [APPS_DIR, ROOT / "config", ROOT / "templates", TESTS_DIR]
SCAN_EXTS = {".py", ".html"}
SKIP_DIR_NAMES = {"__pycache__", "migrations"}

MAX_COMMITS = 15
MAX_TODO_HITS = 40
EXCERPT_CHARS = 260

MODEL_CLASS_RE = re.compile(r"^class\s+(\w+)\s*\(")
TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b[:\s]?(.*)")


def run_git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def extract_title_and_excerpt(path: Path) -> tuple[str, str]:
    text = read_text(path)
    if not text:
        return path.stem, ""

    lines = text.splitlines()
    title = path.stem
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            body_start = i + 1
            break

    excerpt_parts: list[str] = []
    for line in lines[body_start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "|", "```", "---")):
            if excerpt_parts:
                break
            continue
        excerpt_parts.append(stripped)
        if sum(len(p) for p in excerpt_parts) >= EXCERPT_CHARS:
            break

    excerpt = " ".join(excerpt_parts)
    if len(excerpt) > EXCERPT_CHARS:
        excerpt = excerpt[:EXCERPT_CHARS].rsplit(" ", 1)[0] + "…"
    return title, excerpt


def describe_doc(path: Path) -> dict:
    title, excerpt = extract_title_and_excerpt(path)
    stat = path.stat()
    return {
        "path": path,
        "rel": path.relative_to(ROOT).as_posix(),
        "title": title,
        "excerpt": excerpt,
        "mtime": datetime.fromtimestamp(stat.st_mtime),
    }


def git_section() -> str:
    branch = run_git("branch", "--show-current") or "(brak / detached HEAD)"
    log = run_git("log", f"-{MAX_COMMITS}", "--pretty=format:%h %ad %s", "--date=short")
    status = run_git("status", "--porcelain")

    lines = [f"**Branch:** `{branch}`", ""]

    if status:
        dirty = status.splitlines()
        lines.append(f"**Niezacommitowane zmiany ({len(dirty)}):**")
        lines.extend(f"- `{line}`" for line in dirty[:30])
        if len(dirty) > 30:
            lines.append(f"- … oraz {len(dirty) - 30} więcej")
    else:
        lines.append("**Working tree czysty** (brak niezacommitowanych zmian).")

    lines.append("")
    lines.append(f"**Ostatnie {MAX_COMMITS} commitów:**")
    if log:
        lines.extend(f"- `{line}`" for line in log.splitlines())
    else:
        lines.append("- (brak historii / git niedostępny)")

    return "\n".join(lines)


def key_docs_section() -> str:
    lines = []
    for path in KEY_DOCS:
        if not path.exists():
            continue
        doc = describe_doc(path)
        lines.append(f"- **{doc['title']}** (`{doc['rel']}`)")
        if doc["excerpt"]:
            lines.append(f"  {doc['excerpt']}")
    return "\n".join(lines) if lines else "_Brak kluczowych dokumentów._"


def categorize(name: str) -> str:
    lower = name.lower()
    if re.search(r"\d{1,2}[._-]\d{1,2}", lower) or "session" in lower or "podsumowanie" in lower:
        return "Podsumowania sesji"
    if "todo" in lower or "to_do" in lower:
        return "TODO"
    if "plan" in lower:
        return "Plany"
    if "spec" in lower:
        return "Specyfikacje"
    return "Inne"


def handouts_section() -> str:
    if not HANDOUTS_DIR.exists():
        return f"_Katalog `{HANDOUTS_DIR.relative_to(ROOT).as_posix()}` nie istnieje._"

    docs = [describe_doc(p) for p in sorted(HANDOUTS_DIR.glob("*.md"))]
    if not docs:
        return "_Brak plików .md w katalogu handoutów._"

    groups: dict[str, list[dict]] = {}
    for doc in docs:
        groups.setdefault(categorize(doc["path"].name), []).append(doc)

    order = ["Podsumowania sesji", "TODO", "Plany", "Specyfikacje", "Inne"]
    lines = []
    for group_name in order:
        items = groups.get(group_name)
        if not items:
            continue
        items.sort(key=lambda d: d["mtime"], reverse=True)
        lines.append(f"### {group_name}")
        for doc in items:
            date_str = doc["mtime"].strftime("%Y-%m-%d")
            lines.append(f"- **{doc['title']}** (`{doc['rel']}`, {date_str})")
            if doc["excerpt"]:
                lines.append(f"  {doc['excerpt']}")
        lines.append("")
    return "\n".join(lines).strip()


def app_models(models_path: Path) -> list[str]:
    text = read_text(models_path)
    if not text:
        return []
    names = []
    for line in text.splitlines():
        m = MODEL_CLASS_RE.match(line)
        if m and m.group(1) != "Meta":
            names.append(m.group(1))
    return names


def app_commands(app_dir: Path) -> list[str]:
    cmd_dir = app_dir / "management" / "commands"
    if not cmd_dir.exists():
        return []
    return sorted(p.stem for p in cmd_dir.glob("*.py") if p.stem != "__init__")


def apps_section() -> str:
    if not APPS_DIR.exists():
        return "_Brak katalogu apps/._"

    lines = []
    app_dirs = sorted(
        p for p in APPS_DIR.iterdir() if p.is_dir() and not p.name.startswith("__")
    )
    for app_dir in app_dirs:
        models = app_models(app_dir / "models.py")
        commands = app_commands(app_dir)
        py_files = sorted(p.name for p in app_dir.glob("*.py") if p.name != "__init__.py")

        lines.append(f"### `apps/{app_dir.name}`")
        if py_files:
            lines.append(f"- pliki: {', '.join(py_files)}")
        if models:
            lines.append(f"- modele: {', '.join(models)}")
        if commands:
            lines.append(f"- management commands: {', '.join(commands)}")
        lines.append("")
    return "\n".join(lines).strip()


def tests_section() -> str:
    if not TESTS_DIR.exists():
        return "_Brak katalogu tests/._"
    files = sorted(p.name for p in TESTS_DIR.glob("test_*.py"))
    if not files:
        return "_Brak plików testowych._"
    return "\n".join(f"- `tests/{f}`" for f in files)


def config_section() -> str:
    lines = []
    for name, desc in CONFIG_FILES:
        path = ROOT / name
        marker = "✓" if path.exists() else "✗ brak"
        lines.append(f"- `{name}` — {desc} ({marker})")
    return "\n".join(lines)


def todo_section() -> str:
    hits: list[str] = []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir() or path.suffix not in SCAN_EXTS:
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            text = read_text(path)
            if not text:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                m = TODO_RE.search(line)
                if m:
                    rel = path.relative_to(ROOT).as_posix()
                    hits.append(f"- `{rel}:{lineno}` — {m.group(0).strip()}")
                    if len(hits) >= MAX_TODO_HITS:
                        return "\n".join(hits) + (
                            f"\n\n_(limit {MAX_TODO_HITS} wpisów osiągnięty — mogą być kolejne)_"
                        )
    if not hits:
        return "_Brak wpisów TODO/FIXME w kodzie._"
    return "\n".join(hits)


def build_report() -> str:
    now = datetime.now()
    branch = run_git("branch", "--show-current") or "?"

    parts = [
        "# Spis treści projektu — Bileteria UCHO",
        "",
        f"_Wygenerowano automatycznie przez `project_summary.py` — "
        f"{now:%Y-%m-%d %H:%M} (branch: `{branch}`)._",
        "",
        "> Ten plik to punkt wejścia na start sesji: daje obraz stanu repo,",
        "> dokumentów i kodu bez czytania wszystkiego od zera. Szczegóły są",
        "> w plikach źródłowych wskazanych niżej.",
        "",
        "---",
        "",
        "## 1. Status Gita",
        "",
        git_section(),
        "",
        "## 2. Kluczowe dokumenty",
        "",
        key_docs_section(),
        "",
        f"## 3. Handouty, plany i TODO (`{HANDOUTS_DIR.relative_to(ROOT).as_posix()}`)",
        "",
        handouts_section(),
        "",
        "## 4. Aplikacje Django (`apps/`)",
        "",
        apps_section(),
        "",
        "## 5. Testy (`tests/`)",
        "",
        tests_section(),
        "",
        "## 6. Pliki konfiguracyjne i infrastruktura",
        "",
        config_section(),
        "",
        "## 7. TODO / FIXME w kodzie",
        "",
        todo_section(),
        "",
    ]
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="nie zapisuj dodatkowej, oznaczonej datą kopii — tylko LATEST.md",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report = build_report()

    latest_path = OUTPUT_DIR / "LATEST.md"
    latest_path.write_text(report, encoding="utf-8")
    print(f"Zapisano: {latest_path}")

    if not args.no_archive:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        archive_path = OUTPUT_DIR / f"project_summary_{stamp}.md"
        archive_path.write_text(report, encoding="utf-8")
        print(f"Zapisano archiwum: {archive_path}")


if __name__ == "__main__":
    main()
