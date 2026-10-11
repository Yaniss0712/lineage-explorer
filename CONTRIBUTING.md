# Contribuer à Lineage Explorer

Merci de votre intérêt ! Quelques règles simples gardent l'outil utilisable partout, y compris sur des postes sans accès internet ni droits d'installation.

## Principes à respecter

1. **Moteur et serveur : bibliothèque standard Python uniquement** (≥ 3.9). Une librairie externe ne peut être qu'un accélérateur optionnel, importé dans un `try/except` avec un repli fonctionnel.
2. **Interface : aucun CDN, aucun appel réseau.** HTML / CSS / JS vanilla dans `web/`.
3. **Déterministe** : pas de modèle d'IA dans le code livré ; chaque lien proposé doit pouvoir être expliqué.
4. **Aucune donnée réelle** dans le dépôt : uniquement des exemples fictifs (`examples/`, `architecture test/`). Le script `python tools/check_no_data.py` aide à le vérifier avant un commit.
5. **Lisible sans outil** : fonctions courtes, docstrings et messages en français, noms de variables en anglais, PEP 8, type hints.

## Développer

```bash
python -m unittest                                   # tests (stdlib)
python -m engine build --input "architecture test/requetes_architecture.sql"
python -m engine serve                               # http://localhost:8765
python tools/generate_sample.py --domains 50         # jeu fictif plus gros + vérité terrain
```

Le format d'entrée ([docs/04_format_json.md](docs/04_format_json.md)) est un contrat : toute modification doit mettre la documentation à jour. Il en va de même pour les règles de lineage ([docs/05](docs/05_regles_liens_lineage.md)) et l'API ([docs/03](docs/03_architecture.md)).

## Proposer une modification

1. Créez une branche (`feat/...`, `fix/...`).
2. Ajoutez ou adaptez les tests dans `tests/`.
3. Vérifiez que `python -m unittest` passe.
4. Ouvrez une pull request qui explique le besoin et le comportement obtenu.

Pour un bug de parsing SQL, joignez la requête minimale (anonymisée) qui pose problème.
