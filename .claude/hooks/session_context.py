#!/usr/bin/env python3
"""Hook SessionStart: odświeża kontekst projektu i wypisuje go na stdout.

Claude Code dokleja stdout hooka SessionStart do kontekstu sesji, więc nowa
sesja startuje z aktualnym CONTEXT.md i MEMORY.md (wynik build_context.py).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_FILES = ("CONTEXT.md", "MEMORY.md")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "build_context.py")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240,
    )
    print("# Kontekst sesji (hook SessionStart → build_context.py)\n")
    print(result.stdout.strip())
    if result.returncode != 0:
        print(f"\n[build_context.py zakończył się kodem {result.returncode}]")
        print(result.stderr.strip()[-2000:])
    for name in OUTPUT_FILES:
        path = ROOT / "project_context" / name
        if path.exists():
            print(f"\n\n===== project_context/{name} =====\n")
            print(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
