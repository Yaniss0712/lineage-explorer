"""Garde-fou avant commit : refuse de versionner des données réelles (docs/06 § 3.3).

Contrôles sur les fichiers indexés par Git (`git diff --cached --name-only`, ou tous les
fichiers suivis avec `--all`) :
- aucun fichier de `data/`, aucune base `.db`, aucun `.jsonl` hors `examples/` ;
- aucun fichier de plus de 1 Mo ;
- aucun motif sensible listé dans `tools/sensitive_patterns.txt` (fichier local, non versionné :
  une expression régulière par ligne, ex. préfixes de bases réelles, domaines internes).

Usage : `python tools/check_no_data.py [--all]` — code retour 1 si un problème est trouvé.
Hook possible : `.git/hooks/pre-commit` contenant `python tools/check_no_data.py`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_SIZE = 1024 * 1024
ALLOWED_PREFIX = "deploiement/LineageExplorer/app/python/"   # Python portable officiel (signé)
PATTERNS_FILE = os.path.join(ROOT, "tools", "sensitive_patterns.txt")


def staged_files(all_files: bool) -> list[str]:
    cmd = ["git", "ls-files"] if all_files else ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def load_patterns() -> list[re.Pattern]:
    if not os.path.exists(PATTERNS_FILE):
        return []
    with open(PATTERNS_FILE, encoding="utf-8") as handle:
        return [re.compile(line.strip(), re.IGNORECASE) for line in handle
                if line.strip() and not line.startswith("#")]


def check(files: list[str], patterns: list[re.Pattern]) -> list[str]:
    problems = []
    for rel in files:
        path = os.path.join(ROOT, rel)
        norm = rel.replace("\\", "/")
        if norm.startswith("data/") or norm.endswith(".db") or (norm.endswith(".jsonl") and not norm.startswith("examples/")):
            problems.append(f"{rel} : fichier de données, ne doit pas être versionné")
        if norm.endswith("sensitive_patterns.txt") or norm == "config.json":
            problems.append(f"{rel} : fichier local, ne doit pas être versionné")
        if not os.path.isfile(path):
            continue
        if norm.startswith(ALLOWED_PREFIX):
            continue
        if os.path.getsize(path) > MAX_SIZE:
            problems.append(f"{rel} : plus de 1 Mo")
            continue
        if patterns:
            try:
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
            except OSError:
                continue
            for pattern in patterns:
                if pattern.search(text):
                    problems.append(f"{rel} : motif sensible détecté ({pattern.pattern})")
    return problems


def main() -> int:
    try:
        files = staged_files("--all" in sys.argv)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Impossible d'interroger Git : {exc}")
        return 1
    problems = check(files, load_patterns())
    if problems:
        print("⛔ Commit refusé :")
        for p in problems:
            print("  -", p)
        return 1
    print(f"✅ {len(files)} fichier(s) vérifié(s), aucune donnée sensible détectée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
