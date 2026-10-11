# 07 — Roadmap

Les sprints sont exprimés en **lots** ; leur durée sera calée sur le calendrier des sprints d'innovation.

## Vue d'ensemble

| Sprint | Objectif | Livrable démontrable |
|---|---|---|
| **S0 — Cadrage** | Docs, contrat JSON, validations sécurité | Ce dossier + un extrait JSON réel validé par `check` |
| **S1 — Moteur & recherche** | Ingestion, couches, SQLite, recherche | `python -m engine search code_tiers` en ligne de commande |
| **S2 — Lineage & relations** | Inférence du lineage depuis la structure, détection des clés | Rapport de build : X flux inférés, précision P % |
| **S3 — Interface bulles** | Serveur, graphe focalisé, panneau de détail | Navigation en bulles sur données fictives |
| **S4 — Annotations partagées** | URL de spec, validation/rejet, liens manuels | Deux postes voient la même annotation |
| **S5 — Démo** | Lineage colonne, chemin entre tables, polish | Démo du sprint d'inno sur données réelles |

## État d'avancement (2026-10-09)

Implémenté sur données fictives : S1 (ingestion JSON / JSONL, couches, SQLite + FTS5 trigram, CLI), S2 (inférence, flux déclarés, parsing SQL regex, relations N1/N2/N3 + Data Vault, `evaluate`, `build_report.json`), S3 (serveur, recherche, graphe en bulles, détail), S4 (annotations par fichier, valider / rejeter / lien manuel / notes / spec, file de revue), S5 partiel (lineage colonne, chemin entre tables, export CSV). Reste : test sur le poste pro et le volume réel, test à deux postes sur un dossier partagé, `sqlglot` optionnel, export PNG.

## Détail

### S0 — Cadrage
- [ ] Valider ce dossier avec l'équipe.
- [ ] Obtenir l'accord sur l'usage de GitHub (code sans données).
- [ ] Tester le poste cible : Python, SQLite/FTS5, pip.
- [ ] Transmettre le **brief aux data engineers** (doc 09) et obtenir un premier `metadata.json` de niveau 1, même partiel.
- [ ] Demander la **vérité terrain** (20 à 50 flux connus).
- [ ] Vérifier si l'outil ETL Python peut exporter ses flux (niveau 3).
- [ ] Écrire `examples/metadata.sample.json` représentatif (fictif).

### S1 — Moteur & recherche
- [ ] `ingest.py` : JSON + JSON Lines, validation.
- [ ] `layers.py` : attribution des couches par patterns.
- [ ] `store.py` : schéma SQLite, insertion par lots, index, FTS5 (repli `LIKE`).
- [ ] CLI : `check`, `build`, `stats`, `search`.
- [ ] Tests unitaires sur les exemples.

### S2 — Lineage & relations
- [ ] Normalisation des noms + table `column_stats` (fréquence, IDF).
- [ ] ⭐ **`inference.py`** : candidats par couche, couverture pondérée, sélection gloutonne, explications.
- [ ] `evaluate.py` : précision / rappel sur la vérité terrain des data engineers.
- [ ] Import des `flows[]` déclarés et parsing SQL quand ils existent (bonus).
- [ ] Détection des relations de clés : blocking + scoring N1/N2/N3.
- [ ] Règles Data Vault (si conventions confirmées).
- [ ] `build_report.json`.
- [ ] **Mesure de performance sur le volume réel** (objectif < 10 min pour 1 M de colonnes).

### S3 — Interface bulles
- [ ] `server.py` : API (doc 03 § 5) + fichiers statiques.
- [ ] Recherche avec filtres de couche.
- [ ] Graphe Cytoscape focalisé : profondeur, amont/aval, relations, plafond de bulles.
- [ ] Panneau de détail : colonnes, flux, relations, spec.

### S4 — Annotations partagées
- [ ] `annotations.py` : un fichier par objet, fusion par entrée.
- [ ] Interface : URL de spec, valider/rejeter, ajouter un lien, notes.
- [ ] **File de revue** des flux inférés, triée par impact.
- [ ] Test à deux postes sur le dossier partagé.

### S5 — Démo
- [ ] Lineage colonne (sqlglot si disponible, sinon SELECT simples).
- [ ] Plus court chemin entre deux tables.
- [ ] Export CSV de l'analyse d'impact.
- [ ] Scénario de démo répété (doc 01 § 6), captures de secours au cas où.

## MVP minimal pour la démo (si le temps manque)

Recherche + lineage **inféré niveau table** (avec explications et précision mesurée) + bulles + URL de spec. Le reste est présenté comme la suite du projet.

## Évolutions envisagées (après le sprint d'inno)

- Build incrémental (ne recalculer que les objets modifiés).
- Historique des extractions : « qu'est-ce qui a changé depuis la semaine dernière ? ».
- Indicateurs de couverture documentaire par couche / par équipe.
- Import d'autres sources (DDL bruts, exports d'outils ETL, Tableau : quels dashboards utilisent quels marts).
- Hébergement sur un serveur interne pour l'équipe (option C de la doc 03).
