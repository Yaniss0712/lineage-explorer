# 05 — Règles de détection des liens et du lineage

L'outil manipule **deux types de liens**, qu'il ne faut pas confondre :

| | **FLOW** (lineage) | **RELATION** (clé) |
|---|---|---|
| Sens | Orienté : A **alimente** B | Non orienté : A **se joint** à B |
| Question | « D'où vient / où va la donnée ? » | « Comment relier ces tables ? » |
| Exemple | `lac_pret.contrats_raw` → `bv_pret.contrats` | `bv_client.tiers.code_tiers_ref` ↔ `dm_pret.encours.code_tiers_ref` |
| Affichage | Flèche | Trait sans flèche |

Tout est **déterministe** : règles, normalisation, scores, parcours de graphe. Aucune IA.

---

## 0. Hypothèse de travail

Les data engineers fournissent un **JSON de structure** (objets, colonnes, types). Le code SQL des vues et des jobs n'est **pas récupérable de façon systématique**.

Conséquence : le lineage est principalement **inféré** à partir de la structure. Il est :

- **probable**, pas certain : chaque flux porte un **score** de confiance ;
- **explicable** : chaque flux affiche *pourquoi* il est proposé ;
- **validé par l'équipe** : valider / rejeter / compléter dans l'interface.

Si des informations plus fiables sont disponibles (flux déclarés par l'outil ETL, SQL de certaines vues), elles sont utilisées **en priorité** et se cumulent avec l'inférence.

---

## 1. Lineage (FLOW)

### 1.1 Sources, par ordre de fiabilité

| Rang | Origine | Score | Disponibilité |
|---|---|---|---|
| 1 | `MANUAL` | 1.0 | Ajouté dans l'interface |
| 2 | `DECLARED` | 1.0 | `flows[]` du JSON — si l'outil ETL Python expose ses sources/cibles |
| 3 | `SQL_PARSED` | 0.9 | Champ `sql` d'un objet — quand il est disponible |
| 4 | **`INFERRED`** | 0.3 – 0.85 | **Toujours** : calculé à partir de la structure seule — **cœur de l'outil** |

### 1.2 Inférence à partir de la structure (cœur de l'outil)

**Idée :** si une table du BV contient en grande partie les mêmes colonnes (mêmes noms normalisés, mêmes types) qu'une table du lac, et que ces colonnes sont rares dans l'architecture, alors la table du lac l'alimente très probablement.

#### Étape 1 — Le sens vient des couches

L'ordre **LAC → BV → MART** donne la direction du flux. On ne cherche donc les sources d'un objet que dans les couches **en amont** :

| Cible | Sources candidates |
|---|---|
| BV | LAC, et BV (cas des BV construites sur d'autres BV, voir étape 5) |
| MART | BV, et LAC (cas rare, score pénalisé) |
| LAC | Aucune (point d'entrée) |

#### Étape 2 — Blocking (pour tenir le très grand volume)

On ne compare jamais tous les objets entre eux. On construit un **index inversé** `nom_normalisé → objets qui portent cette colonne`, puis, pour chaque cible, on ne garde comme candidats que les objets qui partagent **au moins 2 colonnes non génériques**. Les colonnes techniques (`date_chargement`…) et trop fréquentes sont exclues de l'index.

#### Étape 3 — Correspondance des colonnes

Pour chaque couple (source S, cible C), chaque colonne de C est rapprochée d'une colonne de S :

| Type de correspondance | Exemple | Poids |
|---|---|---|
| Nom normalisé identique | `code_tiers` ↔ `code_tiers_ref` | 1.0 |
| Inclusion de tokens | `montant` ↔ `montant_pret` | 0.6 |
| Similarité `difflib` ≥ 0.85 | `num_contrat` ↔ `numero_contrat` | 0.5 |
| Aucune | `segment` ↔ `categorie_client` (renommage) | 0 → « colonne non expliquée » |

Le type est vérifié : types incompatibles (date ↔ decimal) → la correspondance est annulée.

#### Étape 4 — Score du couple

Chaque colonne est pondérée par sa **rareté** (IDF) : partager `num_contrat`, présent dans 3 tables, est bien plus significatif que partager `libelle`, présent dans 2 000.

```
idf(col)   = log(1 + N_objets / nb_objets_portant(col))

couverture = Σ idf(col de C expliquée) × poids_correspondance
             ───────────────────────────────────────────────
                         Σ idf(toutes les col de C)
```

Score final, borné entre 0 et 1 :

| Signal | Effet |
|---|---|
| **Couverture pondérée** (ci-dessus) | Base du score (× 0.7) |
| Clé de C (`is_key`) retrouvée dans S | + 0.10 |
| Noms d'objets proches après nettoyage (`contrats_raw` → `contrats`) | + 0.10 |
| Domaine commun dans le nom de base (`lac_pret` / `bv_pret`) | + 0.05 |
| Saut de couche (LAC → MART direct) | − 0.15 |
| Moins de 2 colonnes rares en commun | Couple écarté |

Les coefficients sont dans `config.json` (`inference`) pour être ajustés sans toucher au code.

#### Étape 5 — Choix des sources : couverture gloutonne

Un mart est souvent construit à partir de **plusieurs** objets. Pour chaque cible C :

1. Trier les candidats par score.
2. Prendre le meilleur ; marquer les colonnes de C qu'il explique.
3. Reprendre le suivant **uniquement s'il explique des colonnes encore non couvertes** (gain marginal ≥ 10 %).
4. S'arrêter à `max_sources` (5 par défaut) ou quand le gain devient négligeable.

Cela évite qu'une table du lac très large soit proposée comme source de tout.

**Cas BV → BV** : autorisé si le score est élevé et que la source a moins de colonnes que la cible ne lui en emprunte (une BV « enrichie » est construite sur une BV « de base », pas l'inverse). Sinon, le lien est proposé en RELATION.

#### Étape 6 — Explication et lineage colonne

Chaque flux inféré stocke son **explication**, affichée telle quelle dans l'interface :

> Flux proposé (score 0.82) : 3 colonnes sur 4 retrouvées, dont la clé `num_contrat` (rare : 3 objets). Correspondance partielle `montant` ↔ `montant_pret`. Noms d'objets proches (`contrats_raw` → `contrats`), domaine commun `pret`.

Les correspondances de l'étape 3 donnent directement le **lineage colonne inféré**. Les colonnes non expliquées (renommages, calculs) sont listées pour être reliées à la main.

#### Exemple sur le jeu fictif (`examples/`) — résultat d'un prototype

Scores obtenus par un prototype de l'algorithme ci-dessus, sans SQL ni flux déclarés :

| Cible | Source inférée (score) | Colonnes expliquées | Non expliquées → revue humaine |
|---|---|---|---|
| `bv_pret.contrats` | `lac_pret.contrats_raw` (**0.85**) | `num_contrat`, `code_tiers_ref`, `date_signature`, `montant_pret` (partiel) | — |
| `bv_client.tiers` | `lac_crm.clients_raw` (**0.53**) | `code_tiers_ref`, `raison_sociale` | `categorie_client` (renommage de `segment`) |
| `dm_pret.encours` | `bv_client.tiers` (**0.46**) | `code_tiers_ref`, `categorie_client` | `montant_encours` (agrégat `sum(montant_pret)`) |

**Ce que l'exemple montre :**

- Les flux de **copie / nettoyage** (Lac → BV) sont bien trouvés, avec un bon score.
- Les colonnes **renommées ou calculées** ne sont pas expliquées : c'est attendu, et c'est précisément ce que la file de revue fait remonter.
- La seconde source du mart (`bv_pret.contrats`) **n'est pas trouvée**, car elle n'apporte que la clé de jointure et une colonne agrégée. Elle doit être ajoutée à la main (ou déclarée par l'ETL).
- Sur 5 tables, la pondération par rareté joue peu. Sur le volume réel, elle discrimine beaucoup mieux : c'est à mesurer (§ 4).

Seuil d'affichage par défaut des flux inférés : **0.4** (`inference.min_score`). Les flux en dessous restent consultables via le curseur de confiance.

### 1.3 Parsing SQL (si un `sql` est fourni)

Pour chaque objet ayant un `sql` (même partiellement disponible) :

1. Supprimer commentaires et chaînes littérales.
2. Cible : l'objet lui-même (vue) ou `INSERT INTO|OVERWRITE TABLE <cible>`.
3. Sources : identifiants qui suivent `FROM` et `JOIN`, hors alias de CTE.
4. Ne garder que les identifiants présents dans `objects`.

```python
SOURCE_RE = re.compile(r'\b(?:FROM|JOIN)\s+([`"\w]+\.[`"\w]+)', re.IGNORECASE)
```

Si `sqlglot` est installé, il fournit en plus le lineage colonne exact.

### 1.4 Arbitrage entre sources

Pour un même couple (S, C), on garde l'origine **la plus fiable** (MANUAL > DECLARED > SQL_PARSED > INFERRED). Un flux inféré confirmé par le SQL ou l'ETL passe automatiquement en fiable. Un flux déclaré que l'inférence n'aurait pas trouvé est un bon indicateur pour **ajuster les coefficients**.

### 1.5 Parcours amont / aval

BFS sur la table `edges` (kind = `FLOW`), borné en profondeur, avec détection de cycles et filtre de score :

```python
def lineage(start, direction, max_depth, min_score=0.5):
    seen, frontier, result = {start}, [start], []
    for depth in range(1, max_depth + 1):
        nxt = []
        for node in frontier:
            for edge in neighbours(node, direction):          # requête SQLite indexée
                other = edge.source if direction == "up" else edge.target
                if other in seen or is_rejected(edge) or edge.score < min_score:
                    continue
                seen.add(other); nxt.append(other)
                result.append((depth, edge))
        frontier = nxt
        if not frontier:
            break
    return result
```

Le score d'un **chemin** est le produit des scores de ses flux : un lineage Mart → BV → Lac à 0.8 × 0.8 = 0.64 est affiché comme moins sûr qu'un flux direct à 0.8. Un curseur « confiance minimale » dans l'interface permet de filtrer.

---

## 2. Relations de clés (RELATION)

### 2.1 Normalisation des noms

Appliquée à chaque nom de colonne avant comparaison (`normalize.py`), et partagée avec l'inférence de lineage :

1. minuscules, suppression des accents ;
2. séparateurs unifiés (`-`, espace, camelCase → `_`) ;
3. suppression des **préfixes** et **suffixes** de `config.json` (`id_`, `code_`, `_ref`, `_reference`…) ;
4. découpage en **tokens**.

### 2.2 Niveaux de détection

| Niveau | Règle | Exemple | Score de base |
|---|---|---|---|
| **N1 — Exact** | Noms bruts identiques | `id_key` ↔ `id_key` | 0.90 |
| **N2 — Normalisé** | Noms normalisés identiques | `code_tiers` ↔ `code_tiers_reference` | 0.75 |
| **N3 — Partiel** | Inclusion de tokens ou similarité `difflib` ≥ 0.85 | `num_contrat` ↔ `numero_contrat_pret` | 0.55 |
| **Déclaré** | Présent dans `relations[]` du JSON | — | 1.00 |
| **Manuel** | Ajouté dans l'interface | — | 1.00 |

### 2.3 Ajustements du score

| Facteur | Effet |
|---|---|
| Au moins une colonne marquée `is_key` | + 0.10 |
| Types compatibles | + 0.05 |
| Types incompatibles | − 0.30 |
| Nom dans `generic_names` (`id`, `code`, `libelle`…) | − 0.40 |
| Colonne présente dans plus de `max_column_frequency` des objets | − 0.30 |
| Colonne dans `ignore_columns` | lien non créé |
| Les deux objets sont reliés par un FLOW | Le lien n'est **pas** créé en RELATION (il est déjà expliqué par le flux) |

Seuls les liens ≥ `min_score` (0.6 par défaut) sont créés comme **suggérés**.

**Règles ajoutées lors de l'implémentation** (constatées sur les jeux fictifs générés) :

- une relation exige qu'**au moins une des deux colonnes ressemble à une clé** : `is_key`, ou nom correspondant à `key_patterns` (`id_`, `code_`, `num_`, `_ref`…). Sans cette règle, `montant` ↔ `montant` produisait des milliers de « relations » ;
- chaque objet garde au plus `max_relations_per_object` (30) relations suggérées, les meilleures ; les relations déclarées et Data Vault sont toujours gardées ;
- une colonne est « trop fréquente » au-delà de `max(max_column_frequency × N, frequency_floor)` objets, pour que la règle reste pertinente sur un petit jeu de données ;
- l'interface n'affiche que les 12 meilleures relations de l'objet central (les autres sont comptées dans « +N non affichés »).

### 2.4 Conventions Data Vault (bonus)

| Motif | Lien déduit | Score |
|---|---|---|
| `hk_<entite>` dans `hub_<entite>` et dans `sat_<entite>_*` | Hub ↔ Satellite | 0.95 |
| `hk_<entite>` dans `lnk_*` | Hub ↔ Link | 0.95 |

À ajuster aux conventions réelles (`config.json`).

---

## 3. Fusion avec les annotations

À chaque lecture (API), pour chaque lien :

1. Annotation `REJECTED` → lien **masqué** (consultable via un filtre).
2. Annotation `VALIDATED` → lien **validé**, score forcé à 1.0.
3. Annotations `MANUAL` → ajoutées au graphe.
4. Sinon → lien **suggéré** avec son score et son explication.

Un nouveau build ne touche jamais aux annotations : la validation de l'équipe est **conservée d'une extraction à l'autre**, et un flux rejeté ne revient pas.

### File de revue

L'interface propose une **file de revue** des flux inférés, triée pour maximiser l'impact de chaque validation :

1. flux qui alimentent des **marts** (les plus consultés) ;
2. scores **intermédiaires** (0.5 – 0.75), là où l'humain apporte le plus ;
3. objets les plus connectés.

---

## 4. Mesurer la qualité de l'inférence

Pour que le lineage inféré soit crédible (et défendable en démo), on mesure sa qualité sur une **vérité terrain** :

1. Les data engineers listent **20 à 50 flux réels connus** (fichier `data/ground_truth.json`, même format que `flows[]`).
2. `python -m engine evaluate` compare l'inférence à cette liste :
   - **précision** : part des flux proposés qui sont vrais ;
   - **rappel** : part des flux vrais qui sont trouvés ;
   - liste des erreurs, pour ajuster `config.json`.
3. Objectif MVP : **précision ≥ 80 %** au-dessus du seuil d'affichage.

Les flux validés dans l'interface **enrichissent** automatiquement cette vérité terrain au fil du temps.

---

## 5. Rapport qualité du build

À la fin du build (`data/build_report.json`) :

- objets et colonnes par couche ;
- flux par origine (`DECLARED`, `SQL_PARSED`, `INFERRED`) et par tranche de score ;
- relations par niveau (N1, N2, N3…) ;
- objets **orphelins** (BV ou mart sans aucune source trouvée) ;
- taux de colonnes de marts **expliquées** jusqu'au lac ;
- taux de couverture des specs.

Ces indicateurs servent aussi de **slide de démo** : « à partir de la seule structure, l'outil a proposé X flux en Z minutes, avec une précision de P % ».
