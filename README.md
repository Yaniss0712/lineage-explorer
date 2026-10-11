# Lineage Explorer

**Outil open source de data lineage, local et sans dépendance.** Donnez-lui la structure de vos données (un JSON) ou simplement **vos requêtes SQL** : il reconstitue d'où vient chaque donnée, où elle va et comment les tables se joignent, puis l'affiche dans une interface en bulles que votre équipe peut annoter.

> *Open-source, dependency-free data lineage explorer. Feed it a JSON description of your tables or plain SQL scripts; it infers table- and column-level lineage and join keys, and lets your team review it in a local web UI. Python standard library only.*

- **Lac → BV → Mart** : pensé pour les architectures en couches (lake, vues de base, datamarts).
- **Zéro installation** : Python ≥ 3.9 et sa bibliothèque standard, rien d'autre. Pas de `pip install`, pas de CDN, aucun appel réseau.
- **Aucune IA** : tout est déterministe (règles, scores, parcours de graphe), chaque lien proposé est **expliqué**.
- **Grand volume** : index SQLite + affichage focalisé (on n'affiche jamais tout le graphe).
- **Collaboratif** : validations, rejets, liens manuels, URL de spec et notes stockés dans des fichiers JSON partageables, conservés d'un import à l'autre.

---

## Démarrage en 30 secondes

```bash
git clone https://github.com/Yaniss0712/lineage-explorer.git
cd lineage-explorer
python -m engine serve
```

Ouvrez **http://localhost:8765**, cliquez sur **Importer**, puis choisissez un exemple (par exemple `architecture test/requetes_architecture.sql`) ou déposez vos propres fichiers.

## Trois façons de décrire vos données

### 1. Vos requêtes SQL (le plus simple)

Déposez un ou plusieurs fichiers `.sql`, collez du SQL dans l'interface, ou :

```bash
python -m engine build --input mes_requetes.sql        # un fichier
python -m engine build --input dossier_sql/            # tous les .sql d'un dossier
```

| Instruction | Ce que l'outil en déduit |
|---|---|
| `CREATE TABLE t (col type PRIMARY KEY COMMENT '…', …, FOREIGN KEY … REFERENCES …)` | Table, colonnes, types, clés, descriptions, clés étrangères |
| `CREATE [OR REPLACE] VIEW v AS SELECT …` | Vue, ses colonnes et leur type, **lineage colonne par colonne** avec transformations |
| `CREATE TABLE t AS SELECT …` / `INSERT INTO\|OVERWRITE t SELECT …` | Flux sources → cible, colonnes (y compris INSERT positionnel) |
| `ALTER TABLE … ADD FOREIGN KEY …` | Relation de clés |
| `SELECT … FROM a JOIN b ON a.x = b.y` | Tables lues et **jointures** |

Une table seulement lue (jamais créée dans vos scripts) est reconstituée à partir des colonnes que les requêtes y référencent. Le parseur est volontairement simple (expressions régulières, dialectes Hive / Spark / ANSI courants). Il gère alias, `*`, CTE, `CASE`, fonctions et agrégats ; les requêtes très complexes peuvent être partiellement comprises.

### 2. Un JSON de structure

Le niveau minimal (tables + colonnes) suffit. L'outil **infère** alors le lineage par algorithme : colonnes communes pondérées par leur rareté, ordre des couches, sélection gloutonne des sources. Chaque flux reçoit un score et une explication.

```json
{
  "objects": [
    { "id": "lac_pret.contrats_raw", "database": "lac_pret", "name": "contrats_raw", "type": "TABLE",
      "columns": [ { "name": "num_contrat", "type": "string", "is_key": true },
                   { "name": "montant", "type": "decimal(18,2)" } ] },
    { "id": "bv_pret.contrats", "database": "bv_pret", "name": "contrats", "type": "VIEW",
      "columns": [ { "name": "num_contrat", "type": "string" },
                   { "name": "montant_pret", "type": "decimal(18,2)" } ] }
  ]
}
```

On peut y ajouter, facultativement, `flows[]` (flux connus, avec correspondance des colonnes), `relations[]` (jointures connues), `sql` sur un objet, ou `is_key`. Le contrat complet est dans [docs/04_format_json.md](docs/04_format_json.md). Le format JSON Lines (`.jsonl`) est accepté pour les très gros volumes.

### 3. En ligne de commande

```bash
python -m engine check    --input fichier.json|.sql   # valider (erreurs, volumétrie)
python -m engine build    --input fichier.json|.sql   # construire data/lineage.db
python -m engine serve                                # interface web
python -m engine search   code_tiers                  # recherche
python -m engine evaluate --truth verite.json         # précision / rappel de l'inférence
```

## Exemples fournis

| Dossier / fichier | Contenu |
|---|---|
| [`architecture test/`](architecture%20test/README.md) | Banque fictive : **10 tables du lac → 8 BV → 5 marts**. Données en Excel (≤ 5 lignes par table, onglet dictionnaire), [JSON complet](architecture%20test/metadata_architecture.json), [JSON structure seule](architecture%20test/metadata_structure_seule.json), [scripts SQL](architecture%20test/requetes_architecture.sql) et vérité terrain |
| `examples/metadata.sample.json` | Petit exemple complet (flux, SQL, relations) |
| `examples/metadata.structure_only.json` | Même exemple, structure seule |
| `examples/metadata.generated.json` | ~140 objets générés par `tools/generate_sample.py` |

Résultats mesurés sur l'architecture de test :

| Entrée | Flux retrouvés | Colonnes de marts reliées au lac |
|---|---|---|
| Scripts SQL | 28 / 28 | 100 % |
| JSON complet (flux déclarés) | 28 / 28 | 100 % |
| JSON structure seule (inférence) | précision 92 %, rappel 43 % | — |

Le rappel limité de l'inférence pure vient des marts construits à partir d'agrégats (`count`, `sum`). C'est justement ce que la **file « À valider »** et les liens manuels permettent de compléter.

## L'interface

- **Liste des tables** affichée dès l'ouverture, groupée par couche (Lac, BV, Mart) et filtrable.
- **Recherche** partielle, insensible à la casse et aux accents, filtrable par couche et par type de colonne.
- **Graphe** : sources à gauche, cibles à droite ; Amont / Tout / Aval, profondeur, seuil de confiance, jointures et liens rejetés à la demande. Double-clic pour étendre, clic droit pour masquer.
- **Détail d'une table** : colonnes (clic = lineage de la colonne), liens avec origine, score et « pourquoi ? », documentation (URL de spec, notes).
- **File « À valider »** : flux inférés triés par impact, à valider ou rejeter. Un lien rejeté ne revient jamais, même après un nouvel import.
- **Chemin** entre deux tables, **export CSV** de l'analyse d'impact, **mode clair / sombre**.

## Architecture

```
engine/                 Python standard uniquement
├── ingest.py           lecture JSON / JSONL / SQL, validation
├── sql_ingest.py       requêtes SQL → structure, flux, jointures
├── inference.py        inférence du lineage depuis la structure (IDF, couverture, glouton)
├── lineage.py          flux déclarés, parsing SQL, arbitrage, parcours amont/aval
├── relations.py        relations de clés (exact, normalisé, partiel, Data Vault)
├── store.py            SQLite + recherche FTS5 (trigram)
├── annotations.py      annotations partagées (un fichier JSON par objet)
└── server.py           API HTTP locale (127.0.0.1) + fichiers statiques
web/                    HTML / CSS / JS vanilla, graphe SVG maison (aucune librairie)
tests/                  unittest : python -m unittest
```

Documentation détaillée : [vision](docs/01_vision.md) · [spécifications](docs/02_specs_fonctionnelles.md) · [architecture](docs/03_architecture.md) · [format JSON](docs/04_format_json.md) · [règles de lineage](docs/05_regles_liens_lineage.md) · [roadmap](docs/07_roadmap.md) · [guide pour les data engineers](docs/09_brief_data_engineers.md).

## Configuration

Copiez `config.example.json` en `config.json` (ignoré par Git) pour adapter :

- les motifs de nommage des couches (`^lac_`, `^bv_`, `^(dm|mart)_`) ;
- les colonnes techniques à ignorer ;
- les seuils et poids de l'inférence ;
- le dossier des annotations, qui peut être un dossier partagé par l'équipe.

## Contribuer

Les contributions sont bienvenues : voir [CONTRIBUTING.md](CONTRIBUTING.md). Avant toute proposition :

```bash
python -m unittest
```

## Licence

[MIT](LICENSE). Toutes les données présentes dans ce dépôt sont **fictives**.
