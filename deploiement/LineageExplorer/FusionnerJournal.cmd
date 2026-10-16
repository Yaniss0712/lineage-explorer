@echo off
rem Regroupe l'ancien journal (un fichier par modification, versions 0.1 a 0.3) en un seul fichier.
rem Facultatif : la version 0.4 le fait toute seule au premier lancement.
rem A lancer outil ferme sur tous les postes. Les originaux sont gardes dans journal\sauvegardes\.
set "LINEAGE_WORKSPACE=%~dp0"
"%~dp0app\python\python.exe" -m engine compact --workspace "%~dp0."
pause
