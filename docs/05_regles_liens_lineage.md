# 05 — Tags, suggestions et liens

## 1. Principes

- **Aucun lien automatique.** L'outil **suggère** ; l'équipe **crée** les liens (« méthode de lien »), dans le sens qu'elle choisit, puis les **valide** ou les **supprime**.
- **Pas de clés.** Les métadonnées réelles n'ont pas de clé primaire : les suggestions reposent uniquement sur les noms et types de colonnes, et sur les requêtes SQL quand il y en a.
- **Des tags au lieu de couches.** L'équipe définit ses propres catégories (ex. « Source », « Préparation », « Fiche client », « Restitution »), plus fines que Lac / BV / Mart.

## 2. Tags

- **Un tag par objet.** Il peut venir d'un choix à la main (prioritaire) ou d'une **règle de nommage** (`fiche_client_pj_*` → « Fiche client », motif de type `*` / `?`, appliqué au nom ou à l'identifiant).
- Les règles s'appliquent aussi aux objets des imports suivants.
- **Ordre** facultatif (nombre). S'il est renseigné sur deux tags, la suggestion propose le sens **du plus petit vers le plus grand** (ex. Source 1 → Restitution 3). L'utilisateur garde le dernier mot.
- Le catalogue se range en **Tag → Base → Objets**.

## 3. Suggestions

### 3.1 Colonnes communes (toujours)

Deux tables qui partagent des colonnes (même nom après normalisation : minuscules, sans accents, préfixes `id_` `cd_` `code_` `r_` et suffixes `_ref` `_id`… retirés) :

```
rareté(col)    = log(1 + N_objets / nb_objets_portant(col))
recouvrement   = Σ rareté(colonnes communes) / min(Σ rareté(colonnes de A), Σ rareté(colonnes de B))
score          = 0,70 × recouvrement + 0,15 × (noms de tables proches) + 0,15 × min(1, nb_communes / 4)
```

- Ne comptent pas : les **colonnes techniques** (`dt_chargement`, `xx_date_insertion`, `id_tech`…, liste modifiable dans **Réglages**) et les noms **génériques** (`id`, `code`, `url`, `flag`…).
- Une colonne très répandue (`code_tiers` dans 19 tables) pèse peu : à elle seule, elle ne suffit pas à suggérer un lien. C'est le rôle de la **vue colonne** de montrer toutes ces tables.
- Les **types différents** (`int` contre `string`) sont signalés dans l'explication.
- On garde les suggestions de score ≥ 0,30 qui sont dans les 12 meilleures de l'un des deux objets.

### 3.2 Indications du fichier importé

`flows[]` et les requêtes SQL donnent des suggestions **orientées**, avec la correspondance des colonnes déjà préremplie (score 0,95). `relations[]`, les `JOIN` et les clés étrangères SQL donnent des suggestions de **jointure** (0,90).

### 3.3 Affichage

- Dans le graphe : en **pointillés gris**, avec une flèche si un sens est proposé.
- Dans l'onglet **Suggestions** (triées par score) et dans le détail de chaque table.
- **Créer le lien…** ouvre l'éditeur prérempli. **Écarter** la cache définitivement (réversible par l'événement `suggestion.restore`).
- Une suggestion disparaît dès qu'un lien existe entre les deux tables.

## 4. Liens

| | Flux (`FLOW`) | Jointure (`JOIN`) |
|---|---|---|
| Sens | Orienté : A **alimente** B (B **est alimentée par** A) | Non orienté |
| Graphe | Flèche | Trait violet sans flèche |
| Utilisé pour | Amont / aval, impact, lineage colonne, chemin | Chemin entre tables |

- **Deux sens possibles** à la création : « Cette table alimente… » / « … alimente cette table », avec le bouton **⇄** pour inverser.
- **Colonne → colonne (facultatif)** : une ou plusieurs paires, avec transformation (ex. `sum(montant)`). Elles alimentent le lineage d'une colonne.
- **Statuts** : `A_VALIDER` (tirets orange) → `VALIDE` (trait plein). L'auteur peut valider lui-même. Modifier un lien le remet « à valider ».
- **Suppression** à tout moment ; la liste des liens supprimés récemment reste visible dans « À valider ».
- Un lien est marqué **⚠ à revoir** si une table ou une colonne qu'il cite n'existe plus dans le dernier import.

## 5. Vue colonne

Depuis l'onglet **Colonnes** (dictionnaire de toutes les colonnes, avec les types rencontrés) ou le bouton « tables » d'une colonne :

- la colonne au centre, entourée de toutes les tables qui la portent, colorées par tag ;
- option **noms proches** (`code_tiers_lie`, `r_code_tiers`…) ;
- les tables dont le type diffère sont en rouge ;
- on coche deux tables, puis **Créer un lien** ouvre l'éditeur avec la colonne déjà renseignée.

## 6. Mesurer les suggestions

```bash
python -m engine evaluate --truth liens_connus.json --workspace <dossier>
```

`liens_connus.json` : `{"flows": [{"source": "...", "target": "..."}]}`. La comparaison se fait par paire de tables, sans tenir compte du sens.
