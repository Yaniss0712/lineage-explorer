# 04 — Format JSON (contrat d'entrée)

Ce document est le **contrat** entre le script d'extraction (côté pro) et le moteur. Tant que le JSON respecte ce format, on peut changer la façon d'extraire sans toucher au moteur.

## 1. Conventions

| Règle | Exemple |
|---|---|
| Identifiant d'objet = `base.nom`, en minuscules | `bv_client.tiers` |
| Identifiant de colonne = `base.nom.colonne` | `bv_client.tiers.code_tiers` |
| Encodage UTF-8 | — |
| Champs facultatifs : omis ou `null` | — |
| Dates au format ISO 8601 | `2026-10-09T10:35:00+02:00` |

### Niveaux de complétude

Le JSON est fourni par les **data engineers** (brief dédié : doc 09). Seul le niveau 1 est obligatoire ; chaque niveau supplémentaire améliore la fiabilité du lineage.

| Niveau | Contenu | Ce que l'outil en tire |
|---|---|---|
| **1 — Structure** *(obligatoire)* | `objects[]` avec `columns[]` (nom + type) | Recherche, relations de clés, **lineage inféré** |
| 2 — Clés | `is_key` sur les colonnes | Meilleurs scores, moins de faux positifs |
| 3 — Flux ETL | `flows[]` exportés depuis l'outil ETL Python (source → cible) | Lineage **certain** sur les objets couverts |
| 4 — SQL | `sql` pour les vues dont la définition est récupérable | Lineage certain + lineage colonne |

> Un `metadata.json` de niveau 1 suffit pour faire tourner l'outil et la démo. Voir `examples/metadata.structure_only.json`.

## 2. Fichier `metadata.json`

```json
{
  "meta": {
    "format_version": "1.0",
    "extracted_at": "2026-10-09T10:35:00+02:00",
    "source": "hive-prod",
    "extractor": "extract_hive.py v0.1"
  },
  "objects": [
    {
      "id": "lac_crm.clients_raw",
      "database": "lac_crm",
      "name": "clients_raw",
      "type": "TABLE",
      "layer": "LAC",
      "description": "Clients bruts issus du CRM",
      "owner": "equipe_ingestion",
      "sql": null,
      "columns": [
        { "name": "code_tiers", "type": "string", "nullable": false, "is_key": true,
          "description": "Identifiant tiers CRM", "position": 1 },
        { "name": "raison_sociale", "type": "string", "position": 2 }
      ]
    },
    {
      "id": "bv_client.tiers",
      "database": "bv_client",
      "name": "tiers",
      "type": "VIEW",
      "sql": "SELECT trim(upper(code_tiers)) AS code_tiers_ref, raison_sociale FROM lac_crm.clients_raw",
      "columns": [
        { "name": "code_tiers_ref", "type": "string", "is_key": true },
        { "name": "raison_sociale", "type": "string" }
      ]
    }
  ],
  "flows": [
    {
      "source": "lac_crm.clients_raw",
      "target": "bv_client.tiers",
      "job": "job_alim_bv_tiers",
      "column_mapping": [
        { "source": "code_tiers", "target": "code_tiers_ref",
          "transformation": "trim(upper(code_tiers))" }
      ]
    }
  ],
  "relations": [
    {
      "a": "bv_client.tiers", "a_column": "code_tiers_ref",
      "b": "dm_pret.encours", "b_column": "code_tiers_ref",
      "comment": "Jointure documentée dans la spec du mart"
    }
  ]
}
```

### Champs des objets

| Champ | Obligatoire | Description |
|---|---|---|
| `id` | ✅ | `base.nom` en minuscules |
| `database` | ✅ | Base Hive |
| `name` | ✅ | Nom de la table / vue |
| `type` | ✅ | `TABLE` \| `VIEW` \| `EXTERNAL` |
| `layer` | ➖ | `LAC` \| `BV` \| `MART` ; si absent, déduit par les règles de `config.json` |
| `sql` | ➖ | Définition de la vue ou requête d'alimentation — rarement disponible, utilisée quand elle est présente |
| `columns[]` | ✅ | Voir ci-dessous |
| `description`, `owner` | ➖ | Métadonnées libres |

### Champs des colonnes

| Champ | Obligatoire | Description |
|---|---|---|
| `name` | ✅ | Nom de la colonne |
| `type` | ✅ | Type Hive (`string`, `int`, `decimal(18,2)`, `date`…) |
| `is_key` | ➖ | `true` si clé connue (PK, hash key Data Vault…) |
| `nullable`, `description`, `position` | ➖ | Métadonnées libres |

### Flux (`flows[]`) — le vrai lineage

| Champ | Obligatoire | Description |
|---|---|---|
| `source`, `target` | ✅ | Identifiants d'objets |
| `job` | ➖ | Nom du job / script qui fait l'alimentation |
| `column_mapping[]` | ➖ | Correspondances colonne à colonne, avec `transformation` facultative |

> `flows[]` est **facultatif**. Sans lui, le moteur **infère** le lineage à partir de la structure (cf. doc 05 § 1.2), et le déduit du champ `sql` quand il existe. Toutes les sources se cumulent ; la plus fiable l'emporte.

### Relations (`relations[]`)

Liens de clés **déclarés** (connus avec certitude). Ils s'ajoutent aux relations détectées automatiquement.

### Variante grand volume : JSON Lines

Pour un fichier très gros, l'extraction peut produire `metadata.jsonl` : **une ligne = un enregistrement**, avec un champ `record` :

```
{"record":"meta","format_version":"1.0","extracted_at":"2026-10-09T10:35:00+02:00"}
{"record":"object","id":"lac_crm.clients_raw","database":"lac_crm","name":"clients_raw","type":"TABLE","columns":[...]}
{"record":"flow","source":"lac_crm.clients_raw","target":"bv_client.tiers"}
{"record":"relation","a":"bv_client.tiers","a_column":"code_tiers_ref","b":"dm_pret.encours","b_column":"code_tiers_ref"}
```

Le moteur lit ce format ligne par ligne, sans charger le fichier entier en mémoire.

## 3. Fichier d'annotation (un par objet)

Chemin : `<annotations_dir>/<object_id>.json` — exemple : `annotations/bv_client.tiers.json`

```json
{
  "object_id": "bv_client.tiers",
  "spec_url": "https://confluence.exemple.fr/display/DATA/BV+Tiers",
  "notes": [
    { "text": "Le code tiers est re-normalisé ici (trim + upper).",
      "author": "jdupont", "updated_at": "2026-10-09T11:00:00+02:00" }
  ],
  "links": [
    {
      "kind": "RELATION",
      "target": "dm_pret.encours",
      "source_column": "code_tiers_ref",
      "target_column": "code_tiers_ref",
      "status": "VALIDATED",
      "author": "jdupont",
      "updated_at": "2026-10-09T11:02:00+02:00"
    },
    {
      "kind": "RELATION",
      "target": "dm_risque.notation",
      "source_column": "code",
      "target_column": "code",
      "status": "REJECTED",
      "comment": "Faux positif : 'code' générique",
      "author": "jdupont",
      "updated_at": "2026-10-09T11:03:00+02:00"
    }
  ]
}
```

Champs ajoutés automatiquement par le serveur : `spec_author` / `spec_updated_at` (auteur et date de l'URL de spec) et un `id` court sur chaque note (pour pouvoir la supprimer). Un lien de type `FLOW` sans colonnes porte sur l'objet entier ; avec `source_column` / `target_column`, c'est un lien colonne (ex. colonne renommée reliée à la main), et `comment` sert de transformation.

| `status` | Signification |
|---|---|
| `VALIDATED` | Lien suggéré confirmé par un humain |
| `REJECTED` | Lien suggéré refusé — **ne réapparaît plus**, même après un nouveau build |
| `MANUAL` | Lien ajouté à la main, absent de l'extraction |

La clé d'unicité d'un lien est `(kind, source_object, source_column, target, target_column)`.

## 4. Fichier `config.json`

```json
{
  "paths": {
    "input": "data/metadata.json",
    "database": "data/lineage.db",
    "annotations_dir": "data/annotations"
  },
  "layers": [
    { "code": "LAC",  "database_pattern": "^lac_" },
    { "code": "BV",   "database_pattern": "^bv_" },
    { "code": "MART", "database_pattern": "^(dm|mart)_" }
  ],
  "relations": {
    "min_score": 0.6,
    "max_column_frequency": 0.05,
    "ignore_columns": ["date_chargement", "dt_maj", "date_maj", "id_traitement", "flag_actif"],
    "generic_names": ["id", "code", "libelle", "type", "date", "statut"],
    "strip_prefixes": ["id_", "cd_", "code_", "hk_"],
    "strip_suffixes": ["_ref", "_reference", "_id", "_cd", "_hk"]
  },
  "inference": {
    "min_score": 0.4,
    "min_shared_columns": 2,
    "max_sources": 5,
    "min_marginal_gain": 0.10,
    "weights": {
      "coverage": 0.7, "key_found": 0.10, "object_name_similar": 0.10,
      "same_domain": 0.05, "layer_skip_penalty": -0.15
    },
    "object_name_noise": ["_raw", "_hist", "_tmp", "_v"]
  },
  "ui": {
    "default_depth": 2,
    "max_nodes": 150,
    "page_size": 50
  },
  "server": { "host": "127.0.0.1", "port": 8765 },
  "user": { "name": "jdupont" }
}
```

Les **patterns de couches** sont à ajuster selon les conventions réelles de nommage des bases (voir `config.example.json`).

Clés supplémentaires (facultatives, valeurs par défaut dans `engine/config.py`, voir `config.example.json`) :

| Clé | Défaut | Rôle |
|---|---|---|
| `relations.frequency_floor` | 50 | Une colonne n'est « trop fréquente » que si elle dépasse `max(max_column_frequency × N, frequency_floor)` objets |
| `relations.max_bucket_size` | 300 | Groupes de blocking plus grands ignorés (performance) |
| `relations.max_relations_per_object` | 30 | Plafond de relations suggérées par objet |
| `relations.key_patterns` | `^id_`, `^code_`, `^num_`, `_ref$`… | Une relation exige qu'une des deux colonnes soit une clé (`is_key`) ou ait un nom de clé |
| `inference.store_min_score` | 0.2 | Flux inférés conservés en base (visibles via le curseur de confiance) |
| `inference.max_candidates` | 50 | Nombre maximal de sources candidates évaluées par cible |
| `inference.bv_to_bv_min_score` | 0.6 | Score minimal d'un flux BV → BV |

## 5. Validation (`python -m engine check`)

La commande `check` contrôle le JSON avant le build et produit un rapport :

- identifiants dupliqués ;
- `flows` / `relations` pointant vers un objet inexistant ;
- colonnes sans type ;
- objets sans couche (aucun pattern ne correspond) ;
- volumétrie par couche et par base.

## 6. Import depuis l'interface

Le bouton **« Importer »** envoie le fichier à `POST /api/import` (`?format=jsonl` ou `?format=sql` selon l'extension) : même validation que `check`, puis build complet. Le fichier n'est pas copié ailleurs ; seule la base `data/lineage.db` est régénérée. Pour des fichiers de plusieurs centaines de Mo, préférer la ligne de commande.

## 7. Entrée SQL (alternative au JSON)

`--input` accepte aussi un fichier `.sql` ou un dossier de `.sql`. Le module `engine/sql_ingest.py` les traduit dans ce même format :

| Instruction SQL | Traduction |
|---|---|
| `CREATE TABLE` (colonnes, `PRIMARY KEY`, `COMMENT`, `REFERENCES`, `FOREIGN KEY`, `PARTITIONED BY`) | `objects[]` (type `TABLE`), `is_key`, `description`, `relations[]` |
| `CREATE VIEW … AS SELECT` | objet `VIEW` + `flows[]` avec `column_mapping` |
| `CREATE TABLE … AS SELECT`, `INSERT INTO\|OVERWRITE … SELECT` | `flows[]` avec `column_mapping` (objet créé s'il n'est pas déclaré) |
| `ALTER TABLE … ADD FOREIGN KEY` | `relations[]` |
| `… JOIN … ON a.x = b.y` | `relations[]` (commentaire « Jointure lue dans une requête SQL ») |
| table lue mais jamais créée | objet reconstitué avec les colonnes référencées |

Les flux produits portent `"origin": "SQL_PARSED"` (score 0.9, affichés comme « certains »). Ce champ facultatif est aussi accepté dans un JSON ; sans lui, un flux est `DECLARED`. Une table sans nom de base reçoit la base `default`.
