# Architecture de test (fictive)

Banque de détail **fictive** : 23 tables (au plus 5 lignes chacune), organisées en 3 niveaux (données brutes, tables de base, marts) — idéal pour créer des tags et tester l'outil.

```
LAC (10 tables brutes)          BV (8 tables de base)              MART (5 tables métier)
──────────────────────          ─────────────────────              ──────────────────────
lac_crm.clients_raw   ─┐
lac_crm.adresses_raw  ─┴──────▶ bv_client.tiers ─────────────┬───▶ dm_client.vue_360
lac_ref.agences_raw   ─┐                                     ├───▶ dm_pret.encours_client
lac_ref.regions_raw   ─┴──────▶ bv_ref.agences ──────────────┼───▶ dm_reseau.activite_agence
lac_ref.produits_raw  ────────▶ bv_ref.produits ──┐          └───▶ dm_cpt.flux_mensuels
lac_pret.contrats_raw ────────▶ bv_pret.contrats ─┴─▶ bv_pret.contrats_enrichis ─▶ dm_pret.impayes
lac_pret.echeances_raw ───────▶ bv_pret.echeances ───────────────▶ dm_pret.encours_client, dm_pret.impayes
lac_cpt.comptes_raw   ────────▶ bv_cpt.comptes ──────────────────▶ vue_360, activite_agence, flux_mensuels
lac_cpt.operations_raw ─┐
lac_ref.canaux_raw     ─┴─────▶ bv_cpt.operations ───────────────▶ dm_cpt.flux_mensuels
```

Le détail exact (28 flux, colonne par colonne, avec transformations) est dans `00_vue_ensemble.xlsx`, onglets `flux` et `lineage_colonnes`.

## Clés de jointure

| Clé | Tables qui la portent |
|---|---|
| `code_tiers` → `code_tiers_ref` (trim + upper) | clients, adresses, contrats, comptes → tiers, contrats, comptes, contrats_enrichis, 3 marts |
| `code_agence` | clients, agences, contrats, comptes (lac et BV), activite_agence |
| `code_region` | agences_raw, regions_raw, bv_ref.agences |
| `code_produit` | produits, contrats, contrats_enrichis |
| `num_contrat` | contrats, echeances, contrats_enrichis, impayes |
| `num_compte` | comptes, operations, flux_mensuels |
| `code_canal` | operations, canaux |

Les 21 jointures qui ne sont pas déjà expliquées par un flux sont dans l'onglet `jointures` et dans `relations[]` du JSON.

## Pièges volontaires (pour tester l'outil)

- Identifiants tiers « sales » dans le lac (` t001`, `T003 `) normalisés en BV (`T001`).
- Renommages : `segment` → `categorie_client`, `montant` → `montant_pret`, `montant_op` → `montant_operation`.
- Calculs : `mensualite_theorique`, et dans les marts des comptages et sommes (`nb_contrats`, `montant_total_pret`, `montant_impaye`…).
- Une BV construite sur d'autres BV : `bv_pret.contrats_enrichis`.
- Des marts à 3 ou 4 sources.

## Fichiers

| Fichier | Contenu |
|---|---|
| `lac/`, `bv/`, `mart/` | Un Excel par table : onglet `donnees` (tableau Excel) + onglet `dictionnaire` (types, clés, colonne source et transformation) |
| `00_vue_ensemble.xlsx` | Tables, flux, jointures, lineage colonne |
| `metadata_architecture.json` | **JSON complet** pour l'outil : structure + flux déclarés + jointures |
| `metadata_structure_seule.json` | Structure seule (avec clés) : pour tester ce que l'algorithme retrouve seul |
| `verite_terrain.json` | Les 28 vrais flux, pour `python -m engine evaluate` |
| `construire_architecture.py` | Script qui régénère tout (nécessite `openpyxl`) |

Les valeurs des BV et des marts sont calculées à partir des tables sources : elles sont cohérentes entre elles.

## Tester

Dans l'interface : **Importer** → `architecture test/metadata_architecture.json` (ou `…structure_seule.json`).

```bash
python -m engine import --input "architecture test/metadata_structure_seule.json" --workspace data/espace_test
python -m engine evaluate --truth "architecture test/verite_terrain.json" --workspace data/espace_test
```

Structure seule, résultat mesuré : précision 92 %, rappel 43 %. L'algorithme retrouve les copies Lac → BV, mais pas les marts faits d'agrégats. Il faut alors les valider à la main, ou utiliser le JSON complet.
