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

### 2.1 JSON + SQL qui se complètent

Scénario type : on importe d'abord le **JSON de structure** (toutes les tables, colonnes, types), puis le **code SQL** (vues, scripts d'alimentation, requêtes d'analyse). En mode **Compléter** (automatique pour un `.sql` quand une structure existe déjà) :

1. **Reconnaissance des tables** : chaque table du SQL est rattachée à la table existante de même identifiant ou, à défaut, de **même nom** si ce nom est unique. Le SQL omet souvent la base (`FROM fiche_client_pj_segmentation`) ou en cite une autre.
2. **La structure JSON fait foi** : colonnes et types ne sont pas modifiés. Les tables absentes de la structure sont ajoutées (base `default` si le SQL n'en donne pas).
3. **Règles de gestion** ajoutées aux tables concernées (onglet **Règles** du détail) :
   - type de requête (vue, `INSERT`, `CREATE TABLE AS`, requête d'analyse) ;
   - tables lues ; jointures avec leur **condition** (`LEFT JOIN client_fiche_prets ON … AND statut_pret = 'ACTIF'`) ;
   - **filtres** (`WHERE`), regroupement (`GROUP BY`), condition sur groupe (`HAVING`) ;
   - **formule de chaque colonne calculée** (`montant_total_crd = sum(client_fiche_prets.montant_crd)`) ;
   - le code SQL d'origine.

   Les alias (`p.`, `f.`) sont remplacés par les noms de tables.
4. **Suggestions orientées** avec correspondance des colonnes et **lineage de colonne** « lu dans le SQL », toujours sans créer de lien automatiquement.
5. **Rapport de reconnaissance** à l'import : tables reconnues, tables ajoutées, noms ambigus, et **colonnes utilisées dans le SQL mais absentes de la structure** (faute de frappe, colonne supprimée, JSON pas à jour).

Le résultat (structure + SQL) est enregistré comme une nouvelle version des métadonnées dans `metadonnees/`. On peut importer plusieurs fichiers SQL successivement : ils s'additionnent. Le mode **Remplacer** repart d'un fichier unique ; tags, liens et documentation sont conservés dans les deux cas.

Format d'une règle dans le JSON (`rules[]`, produit automatiquement, mais accepté en entrée) :

```json
{ "id": "requete_1", "kind": "INSERT", "target": "db.client_vision_360", "sources": ["db.client_fiche_identite", "db.client_fiche_prets"],
  "from": "db.client_fiche_identite",
  "joins": [ { "type": "LEFT JOIN", "table": "db.client_fiche_prets", "condition": "client_fiche_prets.code_client = client_fiche_identite.code_client" } ],
  "filters": "client_fiche_identite.date_creation >= '2020-01-01'", "group_by": "…", "having": null,
  "columns": [ { "column": "montant_total_crd", "expression": "sum(client_fiche_prets.montant_crd)" } ],
  "sql": "INSERT OVERWRITE TABLE …" }
```

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
