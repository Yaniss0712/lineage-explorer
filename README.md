# Lineage Explorer

**Outil open source de data lineage collaboratif, local et sans dépendance.** Importez la structure de vos données (JSON extrait du metastore ou requêtes SQL). Rangez les tables avec **vos propres tags**, documentez les **liens** entre tables et colonnes à partir des suggestions de l'outil, et partagez le tout avec votre équipe via un simple **dossier Teams**.

> *Open-source, dependency-free, collaborative data lineage tool. Import table structures (JSON or SQL), tag objects with your own categories, create and validate table/column links from suggestions, and share everything through a synced folder (event journal, no conflicts). Ships as a single Windows .exe.*

- **Un exécutable**, `LineageExplorer.exe` (13 Mo) : double-clic, le navigateur s'ouvre. Rien à installer.
- **Collaboratif** : chaque modification est un fichier du journal partagé. Aucun conflit, historique complet, mise à jour automatique chez les collègues, versions enregistrables et restaurables.
- **Vos tags** plutôt que des couches figées. Un tag par objet, règles de nommage, tag en masse.
- **Liens 100 % humains**, aidés par des **suggestions** explicables (colonnes communes, SQL). Statut « à valider », suppression possible, sens au choix, colonne → colonne en option.
- **Vue colonne** : toutes les tables qui portent `code_tiers`, avec les types incohérents signalés.
- **Zéro IA, zéro appel réseau, Python standard** uniquement.

---

## Démarrer

### Avec l'exécutable (recommandé) — rien à installer, rien sur un PC personnel

1. Sur GitHub, ouvrez le dossier [`deploiement/LineageExplorer`](deploiement/LineageExplorer) : il contient `LineageExplorer.exe` et `LISEZMOI.txt`. Téléchargez l'exe (bouton **Download raw file**) ou tout le dépôt (**Code → Download ZIP**).
2. Déposez le dossier `LineageExplorer` (l'exe suffit) dans les fichiers du canal Teams de l'équipe, puis **Synchroniser**.
3. Double-cliquez sur l'exe dans le dossier synchronisé, puis **Importer** votre JSON ou vos `.sql`. Le stockage (`journal\`, `versions\`, `metadonnees\`) se crée tout seul dans ce dossier Teams.

Mode d'emploi complet : [docs/10_deploiement_teams.md](docs/10_deploiement_teams.md).

### Depuis le code (Python ≥ 3.9)

```bash
git clone https://github.com/Yaniss0712/lineage-explorer.git
cd lineage-explorer
python -m engine serve          # espace de travail : data/espace (local)
```

Puis ouvrez http://localhost:8765. Autre espace : `--workspace "C:\chemin\vers\dossier Teams"`.

## Le JSON attendu

Uniquement la structure (aucune clé, aucune couche) :

```json
{ "objects": [
  { "id": "db_prod.client_fiche", "database": "db_prod", "name": "client_fiche", "type": "TABLE",
    "columns": [ { "name": "code_client", "type": "string" }, { "name": "siret", "type": "string" } ] }
] }
```

Des requêtes SQL (`CREATE TABLE`, `CREATE VIEW … AS SELECT`, `INSERT … SELECT`, `SELECT … JOIN`) fonctionnent aussi : elles apportent des suggestions **orientées**, avec la correspondance des colonnes. Détails : [docs/04_format_json.md](docs/04_format_json.md).

## L'interface

| Zone | Contenu |
|---|---|
| **Catalogue** | Tag → Base → Objets (replié) ; sélection multiple pour taguer en masse |
| **Recherche** | Sur tout le catalogue ; `tag:fiche`, `base:db_prod`, `col:code_tiers`, `table:tracking`, combinables |
| **À valider** | Liens créés en attente : valider, modifier, supprimer ; liens supprimés récemment |
| **Suggestions** | Tables probablement liées, avec le « pourquoi » ; créer le lien ou écarter |
| **Colonnes** | Dictionnaire de toutes les colonnes, filtre « types différents », vue colonne |
| **Graphe** | Amont / Tout / Aval, profondeur ; liens validés (plein), à valider (tirets), suggestions (pointillés), jointures |
| **Détail** | Tag, colonnes et lineage d'une colonne, liens, suggestions, documentation (description, propriétaire, URL de spec, notes) |
| **En-tête** | Tags et règles, Historique (versions, activité), Enregistrer la version, Importer, export Excel, rapport, réglages, mode clair / sombre |

## Exemples fictifs

| Fichier | Contenu |
|---|---|
| `examples/metadata.demo_une_base.json` | 20 tables dans **une seule base**, sans clé : même forme qu'un export de metastore réel |
| `architecture test/` | Banque fictive (10 tables brutes → 8 tables de base → 5 marts) : Excel, JSON, SQL |
| `examples/metadata.sample.json`, `metadata.generated.json` | Petits et moyens jeux d'essai |

## Pour les développeurs

```bash
python -m unittest                     # 34 tests (stdlib)
python -m engine import --input fichier.json --workspace data/espace
python -m pip install pyinstaller && python tools/build_exe.py   # → dist/LineageExplorer.exe
```

- **Architecture :** [docs/03_architecture.md](docs/03_architecture.md).
- **Règles de suggestion et de liens :** [docs/05_regles_liens_lineage.md](docs/05_regles_liens_lineage.md).
- **Construction automatique :** le modèle `tools/github-actions-build-exe.yml`, ajouté dans `.github/workflows/`, construit l'exe sur GitHub (lancement manuel, ou tag `v*` → Release).
- **Contribuer :** [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

[MIT](LICENSE). Toutes les données de ce dépôt sont **fictives**. Les données réelles vivent uniquement dans votre espace partagé.
