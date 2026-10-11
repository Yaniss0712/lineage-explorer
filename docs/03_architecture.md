# 03 — Architecture technique

## 1. Vue d'ensemble

Trois étapes séparées, chacune lançable indépendamment :

```
  ┌──────────────────┐     ┌──────────────────────────┐     ┌──────────────────────┐
  │ 1. EXTRACTION    │     │ 2. BUILD (moteur)        │     │ 3. SERVE (interface) │
  │ VS Code, poste   │     │ python -m engine build   │     │ python -m engine     │
  │ pro              │     │                          │     │        serve         │
  │                  │     │ • valide le JSON         │     │                      │
  │ Hive / scripts   │ ──▶ │ • détecte les couches    │ ──▶ │ http.server (stdlib) │
  │ SQL / jobs       │JSON │ • détecte les relations  │SQLite│ API JSON + page web  │
  │      ↓           │     │ • calcule le lineage     │     │ (bulles Cytoscape)   │
  │ metadata.json    │     │ • construit l'index      │     │         ▲            │
  └──────────────────┘     │ → data/lineage.db        │     │         │            │
                           └──────────────────────────┘     │  annotations/*.json  │
                                                            │  (dossier partagé)   │
                                                            └──────────────────────┘
```

**Pourquoi séparer :** l'extraction dépend de l'environnement pro (accès aux bases) ; le build est lourd mais rare (après chaque extraction) ; le serve est léger et utilisé tous les jours.

## 2. Choix techniques

| Composant | Choix | Justification | Alternative écartée |
|---|---|---|---|
| Langage moteur | **Python ≥ 3.9, stdlib uniquement** | `pip` pas toujours disponible sur les postes en entreprise | pandas, networkx (dépendances) |
| Stockage index | **SQLite** (`sqlite3`, stdlib) | Tient des millions de lignes, requêtes rapides, un seul fichier | Tout charger en mémoire (trop gros) |
| Recherche | **SQLite FTS5** si disponible, sinon `LIKE` sur colonnes normalisées | Recherche plein texte rapide sans dépendance | Elasticsearch (lourd) |
| Serveur | **`http.server`** (stdlib) | Aucune installation, local | Flask / FastAPI (pip) |
| Interface | **HTML + JS vanilla + rendu SVG maison** (`web/graph.js`, disposition en couches) | Aucune librairie à embarquer ni à faire valider, aucune compilation ; suffisant pour ≤ 150 bulles | Cytoscape.js (à copier dans `web/vendor/`), React (build), D3 |
| Parsing SQL (lineage colonne) | **Optionnel** : `sqlglot` si installé, sinon parser regex niveau table | Meilleure précision quand c'est possible, sans bloquer | Parser maison complet |
| Annotations | **Fichiers JSON par objet** dans un dossier partagé | Partage simple, peu de conflits | Base partagée (droits réseau) |

> **Règle d'or :** le cœur fonctionne avec la stdlib seule. Toute librairie externe est un **accélérateur optionnel** détecté au démarrage (`try: import sqlglot`).

## 3. Modules du moteur

```
engine/
├── __main__.py      # CLI : check | build | serve | stats | search | evaluate
├── config.py        # lecture config.json, valeurs par défaut
├── ingest.py        # lecture et validation du JSON (streaming si fichier très gros)
├── normalize.py     # normalisation des noms (casse, accents, préfixes/suffixes)
├── layers.py        # attribution de la couche (Lac / BV / Mart) selon les règles
├── sql_ingest.py    # requêtes SQL (DDL, vues, INSERT/CTAS, SELECT) → structure, flux, jointures
├── catalog.py       # objets enrichis + index inversé + fréquences (partagé inférence / relations)
├── build.py         # orchestration du build + build_report.json
├── relations.py     # détection des liens de clés + scoring
├── inference.py     # ⭐ inférence du lineage à partir de la structure (IDF, couverture, glouton)
├── lineage.py       # flux déclarés + parsing SQL + arbitrage + parcours amont/aval
├── evaluate.py      # précision / rappel de l'inférence sur une vérité terrain
├── store.py         # schéma SQLite, écriture, requêtes
├── annotations.py   # lecture/écriture des annotations, fusion avec le build
└── server.py        # API HTTP + fichiers statiques web/
```

## 4. Schéma SQLite (`data/lineage.db`)

```sql
CREATE TABLE objects (
  id TEXT PRIMARY KEY,          -- 'bv_client.tiers'
  layer TEXT,                   -- LAC | BV | MART | AUTRE
  database TEXT, name TEXT, type TEXT,
  description TEXT, owner TEXT, nb_columns INTEGER
);

CREATE TABLE columns (
  id TEXT PRIMARY KEY,          -- 'bv_client.tiers.code_tiers'
  object_id TEXT, name TEXT, name_norm TEXT,
  data_type TEXT, is_key INTEGER, description TEXT, position INTEGER
);

CREATE TABLE edges (
  id INTEGER PRIMARY KEY,
  kind TEXT,                    -- FLOW | RELATION
  source_object TEXT, target_object TEXT,
  source_column TEXT, target_column TEXT,
  origin TEXT,                  -- MANUAL | DECLARED | SQL_PARSED | INFERRED | EXACT | PARTIAL
  score REAL,                   -- 0..1
  explanation TEXT,             -- texte affiché : pourquoi ce lien est proposé
  transformation TEXT, job TEXT
);

CREATE TABLE column_stats (     -- calculée au build, sert à l'inférence
  name_norm TEXT PRIMARY KEY,
  nb_objects INTEGER,           -- nombre d'objets portant ce nom de colonne
  idf REAL
);

CREATE INDEX idx_edges_src ON edges(source_object);
CREATE INDEX idx_edges_tgt ON edges(target_object);
CREATE INDEX idx_columns_norm ON columns(name_norm);
-- + table virtuelle FTS5 sur objects.name, columns.name, descriptions
```

Implémentation (`engine/store.py`) : `edges` porte en plus `details` (JSON : couverture, colonnes non expliquées…) et `parent_id`. Une ligne avec `parent_id IS NULL` est un lien **objet** ; les correspondances de colonnes d'un flux sont des lignes `FLOW` dont `parent_id` pointe vers le flux objet. Les origines de relations sont `EXACT` (N1), `NORMALIZED` (N2), `PARTIAL` (N3), `DECLARED`, `DATAVAULT`. La recherche utilise une table `search` + FTS5 `trigram` (sous-chaînes) si disponible, sinon `LIKE`.

Le statut final d'un lien (validé / rejeté / manuel) n'est **pas** stocké dans la base : il est fusionné **à la lecture** depuis les annotations. Ainsi, un nouveau build n'écrase jamais le travail de l'équipe.

## 5. API HTTP (locale)

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/search?q=&layer=&page=` | Recherche objets + colonnes |
| GET | `/api/object/{id}` | Détail d'un objet (colonnes, liens, annotations fusionnées) |
| GET | `/api/graph/{id}?depth=2&dir=up\|down\|both&rel=1` | Sous-graphe focalisé (≤ 150 nœuds) |
| GET | `/api/lineage/column/{id}?dir=up\|down` | Lineage d'une colonne |
| GET | `/api/path?from=&to=` | Plus court chemin entre deux objets |
| GET | `/api/edge/{id}` | Détail d'un lien (explication, correspondances de colonnes) |
| GET | `/api/review?limit=` | File de revue des flux inférés |
| GET | `/api/impact/{id}.csv` | Export CSV des objets aval (analyse d'impact) |
| GET | `/api/examples` | Liste des JSON fictifs de `examples/` |
| POST | `/api/annotations/{object_id}` | Enregistre URL de spec, note, lien manuel, validation/rejet (`{author, spec_url \| note \| delete_note \| link}`) |
| POST | `/api/import` | Corps = JSON de métadonnées (ou `?example=nom`, `?format=jsonl`) → `check` puis build complet |
| GET | `/api/stats` | Volumétrie, date d'extraction, couverture des specs |

Implémentation : `engine/server.py` (`LineageApi` contient la logique, testable sans réseau).
Le lineage colonne utilise `/api/lineage/column?object=&column=&dir=&min_score=`.

Le serveur écoute **uniquement sur `127.0.0.1`** par défaut.

## 6. Performance « très grand volume »

| Problème | Réponse |
|---|---|
| JSON de plusieurs centaines de Mo | Lecture objet par objet (format **JSON Lines** accepté en plus du JSON classique) |
| Comparer toutes les colonnes entre elles (O(n²)) | **Blocking** : on ne compare que les colonnes qui partagent un nom normalisé ou un token (index inversé) |
| Colonnes génériques présentes partout (`date_maj`, `id`) | Liste d'exclusion + pénalité de fréquence (cf. doc 05) |
| Graphe illisible | Sous-graphe focalisé, profondeur bornée, plafond de 150 bulles |
| Parcours amont/aval | BFS sur la table `edges` indexée, résultat paginé |
| Inférence du lineage sur des milliers d'objets | Candidats limités aux objets partageant ≥ 2 colonnes rares (index inversé), uniquement dans les couches amont |
| Build long | Build incrémental en V2 (hash par objet, on ne recalcule que ce qui change) |

**Cible MVP** : build de 1 million de colonnes en **< 10 min** sur un poste standard ; requêtes interface **< 2 s**.

## 7. Partage en équipe

Les annotations contiennent de vrais noms de tables : elles **ne vont pas sur GitHub**.

| Option | Principe | Avantages | Limites |
|---|---|---|---|
| **A. Dossier partagé** *(recommandée MVP)* | `annotations_dir` pointe vers un partage réseau / SharePoint synchronisé | Simple, aucun droit spécial | Conflits si deux personnes éditent le même objet au même moment |
| B. Repo Git interne | Annotations versionnées dans un dépôt interne (GitLab/Azure DevOps du client) | Historique, revue | Suppose un Git interne et l'aisance de l'équipe avec Git |
| C. Serveur unique | Une machine héberge l'outil, l'équipe s'y connecte | Une seule source de vérité | Droits réseau / pare-feu, machine allumée en permanence |

**Gestion des conflits (option A)** : un fichier par objet (`annotations/bv_client.tiers.json`) ; chaque entrée porte `author` + `updated_at` ; à l'écriture, le serveur relit le fichier, fusionne entrée par entrée et réécrit (dernier écrit gagne **par entrée**, pas pour le fichier entier).

## 8. Sécurité

- Lecture de **structure uniquement**, jamais de contenu de données.
- Serveur lié à `127.0.0.1`, aucun appel sortant.
- Les URL de spec sont affichées comme liens, **jamais chargées** par l'outil.
- Échappement systématique du HTML dans l'interface (noms et descriptions).
