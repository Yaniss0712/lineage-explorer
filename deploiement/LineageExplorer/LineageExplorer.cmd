@echo off
rem Lineage Explorer : lance l'outil (Python portable inclus, aucune installation).
rem L'espace partage est le dossier de ce fichier (journal\, versions\, metadonnees\).
set "LINEAGE_WORKSPACE=%~dp0"
start "" "%~dp0app\python\pythonw.exe" "%~dp0app\lineage_explorer.py"
