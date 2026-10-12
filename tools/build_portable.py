"""Fabrique `deploiement/LineageExplorer/` : la version portable à déposer dans Teams.

Contenu produit :
    LineageExplorer.cmd        lanceur (double-clic)
    LISEZMOI.txt
    app/                       code de l'outil (engine/, web/, exemples)
    app/python/                Python officiel (exécutables signés par la Python Software
                               Foundation) + bibliothèque standard zippée + Tcl/Tk

Pourquoi pas un .exe PyInstaller : Microsoft Defender classe souvent ces exe non signés
comme menaces (faux positif « Trojan:Win32/Bearfoos.A!ml ») et les supprime ou les arrête.
Le Python officiel signé, lui, n'est pas concerné.

À lancer sur un poste Windows de développement ayant Python ≥ 3.9 installé (le runtime
est copié depuis cette installation) :

    python tools/build_portable.py
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(ROOT, "deploiement", "LineageExplorer")
PY_HOME = sys.base_prefix
VERSION = f"{sys.version_info.major}{sys.version_info.minor}"

RUNTIME_FILES = ["python.exe", "pythonw.exe", "python3.dll", f"python{VERSION}.dll", "vcruntime140.dll",
                 "vcruntime140_1.dll", "LICENSE.txt"]
DLLS = ["_socket.pyd", "select.pyd", "_sqlite3.pyd", "sqlite3.dll", "unicodedata.pyd", "_queue.pyd",
        "_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll", "_uuid.pyd", "_zoneinfo.pyd", "_decimal.pyd",
        "_ctypes.pyd", "libffi-8.dll", "pyexpat.pyd"]
STDLIB_EXCLUDE = {"test", "idlelib", "ensurepip", "turtledemo", "site-packages", "venv", "pydoc_data", "lib2to3",
                  "__pycache__", "tests", "__phello__", "_pyrepl", "distutils"}
APP_ITEMS = [("engine", "engine"), ("web", "web"), ("lineage_explorer.py", "lineage_explorer.py")]
EXAMPLES = ["metadata.demo_une_base.json", "metadata.sample.json"]

LAUNCHER = r"""@echo off
rem Lineage Explorer : lance l'outil (Python portable inclus, aucune installation).
rem L'espace partage est le dossier de ce fichier (journal\, versions\, metadonnees\).
set "LINEAGE_WORKSPACE=%~dp0"
start "" "%~dp0app\python\pythonw.exe" "%~dp0app\lineage_explorer.py"
"""

README = r"""LINEAGE EXPLORER — DOSSIER À DÉPOSER DANS TEAMS
=================================================

Ce dossier est prêt à l'emploi : copiez-le TEL QUEL (avec le sous-dossier « app ») dans
les fichiers du canal Teams ou dans tout dossier partagé de l'équipe. Rien à installer.

1. Teams : canal de l'équipe > Fichiers > Charger > Dossier > ce dossier « LineageExplorer ».
2. « Synchroniser » pour le voir dans l'Explorateur Windows, puis clic droit sur le dossier
   > « Toujours conserver sur cet appareil » (indispensable : l'outil lit ses fichiers en local).
3. Double-cliquer sur LineageExplorer.cmd.
   Le navigateur s'ouvre et une petite fenêtre « Lineage Explorer est ouvert » apparaît :
   la garder ouverte (la fermer arrête l'outil). Relancer le .cmd rouvre simplement l'onglet.
4. Première fois : bouton « Importer » > le JSON extrait du metastore (ou des .sql).

Créés automatiquement dans ce dossier (ne pas modifier ni supprimer à la main) :
   metadonnees\   les fichiers importés
   journal\       une modification = un petit fichier (tags, liens, specs, notes…)
   versions\      les « Enregistrer » (restaurables depuis « Historique »)

Sur chaque poste, seul un index de recherche est recréé automatiquement dans
%LOCALAPPDATA%\LineageExplorer (avec le journal technique lineage_explorer.log).

« app\python » est le Python officiel (fichiers signés par la Python Software Foundation),
utilisé uniquement par cet outil. Mise à jour : fermer l'outil partout, remplacer le dossier
« app » et LineageExplorer.cmd ; journal\, versions\ et metadonnees\ ne bougent pas.

Guide complet : docs/10_deploiement_teams.md sur GitHub (Yaniss0712/lineage-explorer).
"""


def _clean_dest() -> None:
    """Supprime l'ancienne version (code et runtime), jamais les données d'un espace."""
    os.makedirs(DEST, exist_ok=True)
    for name in ("app", "LineageExplorer.exe", "LineageExplorer.cmd", "LISEZMOI.txt"):
        path = os.path.join(DEST, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)


def _copy_runtime(py_dir: str) -> None:
    os.makedirs(py_dir)
    for name in RUNTIME_FILES:
        shutil.copy2(os.path.join(PY_HOME, name), py_dir)
    for name in DLLS:
        src = os.path.join(PY_HOME, "DLLs", name)
        if os.path.exists(src):
            shutil.copy2(src, py_dir)
    if os.path.isdir(os.path.join(PY_HOME, "tcl")):
        shutil.copytree(os.path.join(PY_HOME, "tcl"), os.path.join(py_dir, "tcl"),
                        ignore=shutil.ignore_patterns("demos", "*.lib", "tzdata"))
    lib = os.path.join(PY_HOME, "Lib")
    with zipfile.ZipFile(os.path.join(py_dir, f"python{VERSION}.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(lib):
            dirs[:] = [d for d in dirs if d not in STDLIB_EXCLUDE]
            for name in files:
                if name.endswith(".py"):
                    full = os.path.join(folder, name)
                    z.write(full, os.path.relpath(full, lib))
    with open(os.path.join(py_dir, f"python{VERSION}._pth"), "w", encoding="utf-8") as handle:
        handle.write(f"python{VERSION}.zip\n.\n..\n")


def _copy_app(app_dir: str) -> None:
    for src, dst in APP_ITEMS:
        source = os.path.join(ROOT, src)
        if os.path.isdir(source):
            shutil.copytree(source, os.path.join(app_dir, dst), ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, os.path.join(app_dir, dst))
    os.makedirs(os.path.join(app_dir, "examples"))
    for name in EXAMPLES:
        shutil.copy2(os.path.join(ROOT, "examples", name), os.path.join(app_dir, "examples"))


def main() -> int:
    if os.name != "nt":
        print("À lancer sous Windows (le runtime Python Windows est copié).")
        return 1
    _clean_dest()
    app_dir = os.path.join(DEST, "app")
    os.makedirs(app_dir)
    _copy_runtime(os.path.join(app_dir, "python"))
    _copy_app(app_dir)
    with open(os.path.join(DEST, "LineageExplorer.cmd"), "w", encoding="ascii", newline="\r\n") as handle:
        handle.write(LAUNCHER)
    with open(os.path.join(DEST, "LISEZMOI.txt"), "w", encoding="utf-8-sig", newline="\r\n") as handle:
        handle.write(README)
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(DEST) for f in fs)
    print(f"Version portable : {DEST} ({size / 1e6:.1f} Mo, Python {sys.version.split()[0]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
