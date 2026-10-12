# 04 — Formats d'entrée et espace partagé

## 1. Métadonnées importées (JSON)

Le minimum : la liste des objets (tables / vues) et de leurs colonnes. Aucune clé, aucune couche n'est attendue.

```json
{
  "meta": { "extracted_at": "2026-10-13T10:00:00+02:00", "source": "hive-prod" },
  "objects": [
    {
      "id": "db_demo_dw.client_fiche_identite",
      "database": "db_demo_dw",
      "name": "client_fiche_identite",
      "type": "TABLE",
      "description": "facultatif",
      "columns": [
        { "name": "code_client", "type": "string" },
        { "name": "siret", "type": "string", "description": "facultatif" }
      ]
    }
  ]
}
```

| Champ | Obligatoire | Remarque |
|---|---|---|
| `objects[].id` | ✅ (ou `database` + `name`) | `base.nom`, mis en minuscules |
| `objects[].type` | ➖ | `TABLE` (défaut), `VIEW`… |
| `objects[].columns[].name` / `.type` | ✅ / conseillé | Le type sert à repérer les incohérences (`int` contre `string`) |
| `description`, `owner`, `sql` | ➖ | — |
| `layer`, `is_key` | ➖ | Anciennes versions : acceptés mais **ignorés** (les tags remplacent les couches) |

Champs facultatifs qui deviennent des **suggestions** (jamais des liens automatiques) :

```json
"flows": [ { "source": "a.t1", "target": "a.t2", "job": "nom_job",
             "column_mapping": [ { "source": "x", "target": "y", "transformation": "upper(x)" } ] } ],
"relations": [ { "a": "a.t1", "a_column": "id", "b": "a.t2", "b_column": "id_t1", "comment": "…" } ]
```

Variante **JSON Lines** (`.jsonl`) pour les très gros volumes : une ligne par enregistrement, avec `"record": "object" | "flow" | "relation" | "meta"`.

## 2. Requêtes SQL

Un fichier `.sql`, plusieurs fichiers, ou du SQL collé dans l'interface (module `engine/sql_ingest.py`) :

| Instruction | Ce que l'outil en tire |
|---|---|
| `CREATE TABLE` (colonnes, types, `COMMENT`, `PARTITIONED BY`) | Tables, colonnes, descriptions |
| `CREATE VIEW … AS SELECT`, `CREATE TABLE … AS SELECT`, `INSERT … SELECT` | Objets + **suggestions orientées** avec correspondance des colonnes et transformations |
| `… JOIN … ON a.x = b.y`, `FOREIGN KEY`, `REFERENCES` | Suggestions de **jointure** |
| Table lue mais jamais créée | Objet reconstitué avec les colonnes utilisées |

## 3. Espace partagé (dossier Teams)

Voir [10 — Déploiement Teams](10_deploiement_teams.md). Trois dossiers, créés automatiquement :

- `metadonnees/` : chaque import, normalisé (JSON) ; le plus récent est celui utilisé ;
- `journal/` : un fichier JSON par modification ;
- `versions/` : les instantanés « Enregistrer la version ».

### Événement du journal

```json
{ "id": "a1b2c3d4e5f6", "ts": "2026-10-13T08:12:03.123456+00:00", "user": "jdupont",
  "type": "link.create",
  "data": { "id": "9f8e7d6c5b4a", "kind": "FLOW", "source": "db.t1", "target": "db.t2",
            "columns": [ { "source": "code_client", "target": "code_client", "transformation": null } ],
            "comment": "", "suggestion_key": "db.t1|db.t2" } }
```

| Type | Données |
|---|---|
| `tag.save` / `tag.delete` | `id`, `name`, `color`, `description`, `order` |
| `object.tag` | `object_ids[]`, `tag_id` (null = retirer le tag choisi à la main) |
| `rule.save` / `rule.delete` | `id`, `pattern` (ex. `fiche_client_*`), `tag_id` |
| `link.create` / `link.update` | `id`, `kind` (`FLOW` = flux orienté, `JOIN` = jointure), `source`, `target`, `columns[]`, `comment` |
| `link.validate` / `link.delete` | `id` (+ `reason` facultatif) |
| `suggestion.dismiss` / `suggestion.restore` | `key` (`objetA|objetB`, ordre alphabétique) |
| `object.update` | `id`, `description`, `owner`, `spec_url` |
| `note.add` / `note.delete` | `object_id`, `id`, `text` |
| `settings.update` | `ignore_columns[]`, `hidden_objects[]` |
| `metadata.import` | `file`, `name`, `stats`, `diff` |
| `version.save` / `version.restore` | `file`, `label`, `comment` |

L'état de l'équipe = rejeu de tous les événements, triés par date (`ts`, puis `id`). Un lien créé a le statut `A_VALIDER`. `link.validate` le passe à `VALIDE` (son auteur peut le valider lui-même). `link.delete` le retire, en gardant la trace de qui et quand.

## 4. `config.json` (facultatif)

À côté de l'exe (ou à la racine du projet en développement). Voir `config.example.json`. Clés principales :

| Clé | Défaut | Rôle |
|---|---|---|
| `paths.workspace` | dossier de l'exe | Espace partagé ; `%USERPROFILE%` accepté |
| `paths.cache_dir` | `%LOCALAPPDATA%\LineageExplorer\cache` | Index local (jamais dans Teams) |
| `suggestions.ignore_columns` | colonnes techniques | Complétées par les réglages de l'équipe dans l'interface |
| `suggestions.min_score` / `max_per_object` | 0.30 / 12 | Sensibilité des suggestions |
| `ui.refresh_seconds` | 15 | Fréquence de relecture du journal |
