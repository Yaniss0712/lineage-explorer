"""Construit `dist/LineageExplorer.exe` (Windows) avec PyInstaller.

À lancer sur un poste de développement (PyInstaller n'est nécessaire que pour construire,
pas pour utiliser l'exe) :

    python -m pip install pyinstaller
    python tools/build_exe.py

L'exe embarque Python, le moteur, l'interface web et les exemples fictifs. Il ne contient
aucune donnée de l'équipe : celles-ci vivent dans le dossier où l'exe est posé (espace partagé).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEP = ";" if os.name == "nt" else ":"


def main() -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller absent : python -m pip install pyinstaller")
        return 1
    data = [("web", "web"), ("examples/*.json", "examples"),
            ("architecture test/*.json", "architecture test"), ("architecture test/*.sql", "architecture test")]
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
           "--name", "LineageExplorer", "--distpath", os.path.join(ROOT, "dist"),
           "--workpath", os.path.join(ROOT, "build"), "--specpath", os.path.join(ROOT, "build")]
    for src, dest in data:
        cmd += ["--add-data", f"{os.path.join(ROOT, src)}{SEP}{dest}"]
    icon = os.path.join(ROOT, "tools", "lineage.ico")
    if os.path.exists(icon):
        cmd += ["--icon", icon]
    cmd.append(os.path.join(ROOT, "lineage_explorer.py"))
    print(" ".join(cmd))
    result = subprocess.call(cmd, cwd=ROOT)
    if result == 0:
        exe = os.path.join(ROOT, "dist", "LineageExplorer.exe")
        print(f"\nExécutable : {exe} ({os.path.getsize(exe) / 1e6:.1f} Mo)")
        shutil.rmtree(os.path.join(ROOT, "build"), ignore_errors=True)
    return result


if __name__ == "__main__":
    sys.exit(main())
