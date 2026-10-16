# CLAUDE.md — Contexte de co-développement

Ce fichier donne à un assistant de code (Claude Code) le contexte nécessaire pour contribuer au projet. Les contributeurs humains peuvent lire [CONTRIBUTING.md](CONTRIBUTING.md).

## Le projet

Outil de lineage de données à très grand volume. Il ingère un JSON de structure et des requêtes SQL (sqlglot dans `vendor/`, repli regex), crée les liens lus dans le SQL, propose des suggestions, et l'équipe crée / valide les liens, tague les objets (l'ordre des tags = sens d'alimentation et colonnes du graphe), crée ses propres objets (New), documente et suit les anomalies, via un journal partagé (dossier Teams, fichiers `.jsonl` par version et par poste, annulation par `event.revert`). Détails : `docs/`.

## Contraintes impératives

1. **`engine/` et `server` : bibliothèque standard Python uniquement** (≥ 3.9). Toute librairie externe est optionnelle, importée dans un `try/except`, avec un repli fonctionnel.
2. **Aucun CDN, aucun appel réseau** dans `web/`. Les librairies JS sont copiées dans `web/vendor/`.
3. **Aucune IA** dans le code livré : tout est déterministe.
4. **Aucune donnée réelle** : on ne travaille que sur des exemples fictifs (`examples/`, `architecture test/`). Ne jamais inventer de noms qui ressemblent à de vraies bases d'entreprise.
5. Le code doit rester **lisible et modifiable sans IA** : fonctions courtes, docstrings en français, pas de magie.

## Conventions

- Langue : **français** pour la doc, les docstrings, les commentaires et les messages utilisateur ; noms de variables en anglais.
- Identifiants : `base.objet` et `base.objet.colonne`, en minuscules.
- Le format d'entrée est défini dans `docs/04_format_json.md` : c'est le contrat, ne pas le changer sans mettre la doc à jour.
- Tests : `unittest` (stdlib) dans `tests/`, lancés par `python -m unittest`.
- Style : PEP 8, type hints, pas de classe quand une fonction suffit.

## Commandes

```bash
python -m unittest
python -m engine check --input examples/metadata.demo_une_base.json
python -m engine import --input examples/metadata.demo_une_base.json --workspace data/espace
python -m engine serve --workspace data/espace
python tools/build_exe.py            # nécessite pyinstaller (poste de développement)
```

## Avant chaque commit

- `python tools/check_no_data.py`
- Doc mise à jour si le format JSON, l'API ou les règles changent.
