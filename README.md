# Lineage Explorer

**Outil open source de data lineage collaboratif, local et sans dépendance.** Importez la structure de vos données (JSON extrait du metastore ou requêtes SQL). Rangez les tables avec **vos propres tags**, documentez les **liens** entre tables et colonnes à partir des suggestions de l'outil, et partagez le tout avec votre équipe via un simple **dossier Teams**.

> *Open-source, dependency-free, collaborative data lineage tool. Import table structures (JSON or SQL), tag objects with your own categories, create and validate table/column links from suggestions, and share everything through a synced folder (event journal, no conflicts). Ships as a portable folder with the official signed Python runtime.*

- **Un dossier à déposer dans Teams** (20 Mo, Python portable officiel inclus) : double-clic sur `LineageExplorer.cmd`, le navigateur s'ouvre. Rien à installer.
- **Collaboratif** : chaque modification est une ligne du journal partagé (un fichier par version et par poste). Aucun conflit, historique complet, **Ctrl+Z / Ctrl+Y**, retour à n'importe quel point du journal, mise à jour automatique chez les collègues, versions enregistrables et restaurables.
- **Vos tags** plutôt que des couches figées. Un tag par objet, règles de nommage, tag en masse. **L'ordre des tags** donne le sens d'alimentation et la disposition du graphe : ce qui alimente à gauche, ce qui est alimenté à droite, une colonne colorée par tag.
- **Le SQL construit la carte** : analyseur complet (sqlglot, inclus, pur Python) qui comprend CTE, sous-requêtes, `UNION`, `CASE`, dialecte Hive. Il reconnaît les tables, le sens des flux, les jointures et les **règles de gestion**, et crée les liens dans le graphe (flux à valider, jointures validées).
- **Vos propres objets** : objet métier à l'origine des données, viz finale, table prévue… avec l'étiquette **New**, à valider tels quels ou à **fusionner** plus tard avec leur vraie définition (SQL ou JSON), liens conservés et sans doublon.
- **Liens** créés à la main ou lus dans le SQL, aidés par des **suggestions** explicables. Statut « à valider », suppression possible, colonne → colonne en option. Raccourcis : **Entrée** valide, **Suppr / E** supprime.
- **Vue colonne** : toutes les tables qui portent `code_tiers`, et sa **chaîne d'existence** (viz ← mart ← BV ← lac ← objet métier). **Vue d'ensemble** d'un périmètre (un bloc par tag), **vue 360** d'une table.
- **Suivi** des anomalies, demandes de modification et améliorations de l'outil, avec détection automatique (types incohérents, liens orphelins, tables New devenues réelles…).
- **Zéro IA, zéro appel réseau, Python standard** (+ sqlglot fourni dans `vendor/`).

---

## Démarrer

### Version portable (recommandé) — rien à installer, rien sur un PC personnel

1. Sur GitHub : **Code → Download ZIP**, puis dézipper.
2. Déposer le dossier [`deploiement/LineageExplorer`](deploiement/LineageExplorer) **tel quel** dans les fichiers du canal Teams, puis **Synchroniser** et **Toujours conserver sur cet appareil**.
3. Double-cliquer sur `LineageExplorer.cmd`, puis **Importer** votre JSON ou vos `.sql`. Le stockage (`journal\`, `versions\`, `metadonnees\`) se crée tout seul dans ce dossier Teams.

Le dossier contient le Python officiel (signé par la Python Software Foundation) : pas d'installation, et pas de `.exe` maison que l'antivirus confondrait avec une menace.

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

**JSON et SQL se complètent** : importez d'abord la structure en JSON, puis votre code SQL. L'outil reconnaît les tables existantes (même sans le nom de base), leur ajoute les **règles de gestion** (jointures et conditions, filtres, regroupements, formules des colonnes) et signale les colonnes inconnues. Les flux lus dans le SQL deviennent des **liens visibles dans le graphe** (à valider), les jointures des liens validés ; réimporter le même SQL ne crée pas de doublon, et un lien supprimé par l'équipe n'est pas recréé. Réimporter un JSON de structure ne perd rien de ce que le SQL a apporté. Script d'extraction Hive : [`tools/extract_hive_metadata.py`](tools/extract_hive_metadata.py) (structure JSON + SQL des vues en un lancement). Détails : [docs/04_format_json.md](docs/04_format_json.md).

## L'interface

| Zone | Contenu |
|---|---|
| **Catalogue** | Tag → Base → Objets (replié) ; sélection multiple pour taguer en masse ; **+ Objet** pour créer un objet métier, une viz, une table prévue |
| **Recherche** | Sur tout le catalogue ; `tag:fiche`, `base:db_prod`, `col:code_tiers`, `table:tracking`, combinables |
| **À valider** | Liens créés en attente : valider, modifier, supprimer ; liens supprimés récemment |
| **Suggestions** | Tables probablement liées, avec le « pourquoi » ; créer le lien ou écarter |
| **Colonnes** | Dictionnaire de toutes les colonnes, filtre « types différents », vue colonne et chaîne d'existence |
| **Suivi** | Anomalies, modifications, améliorations (statut, priorité, commentaires) ; anomalies détectées automatiquement |
| **Graphe** | Une colonne par tag dans l'ordre ; Amont / Tout / Aval, profondeur, 360° ; liens validés (plein), à valider (tirets), suggestions (pointillés), jointures, pastille SQL ; bouton retour et fil d'Ariane, zoom, mini-carte, « trouver dans le graphe », panneaux repliables ; les branches ouvertes restent ouvertes |
| **Détail** | Tag, colonnes et lineage d'une colonne, liens, suggestions, **règles de gestion lues dans le SQL**, documentation (description, propriétaire, URL de spec, notes) |
| **En-tête** | Annuler / rétablir, Vue d'ensemble, Tags et règles, Historique (journal avec retour dans le temps, versions), Enregistrer la version, Importer (et fusionner une table New), export Excel, rapport, réglages, raccourcis ( ? ), mode clair / sombre |

## Exemples fictifs

| Fichier | Contenu |
|---|---|
| `examples/metadata.demo_une_base.json` | 20 tables dans **une seule base**, sans clé : même forme qu'un export de metastore réel |
| `architecture test/` | Banque fictive (10 tables brutes → 8 tables de base → 5 marts) : Excel, JSON, SQL ; `requetes_complexes.sql` + `verite_terrain_sql.json` (CTE, UNION, sous-requêtes) ; [fiche de recette v0.4](architecture%20test/RECETTE_v0.4.md) |
| `examples/metadata.sample.json`, `metadata.generated.json` | Petits et moyens jeux d'essai |

## Pour les développeurs

```bash
python -m unittest                     # 74 tests (stdlib)
python -m engine import --input fichier.json --workspace data/espace
python tools/build_portable.py        # → deploiement/LineageExplorer (version portable)
```

- **Architecture :** [docs/03_architecture.md](docs/03_architecture.md).
- **Règles de suggestion et de liens :** [docs/05_regles_liens_lineage.md](docs/05_regles_liens_lineage.md).
- **Contribuer :** [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

[MIT](LICENSE). Toutes les données de ce dépôt sont **fictives**. Les données réelles vivent uniquement dans votre espace partagé.
