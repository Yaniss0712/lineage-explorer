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

## État d'avancement (2026-10-13) — version 0.2

Réalisé : tags définis par l'équipe (règles de nommage, tag en masse) à la place des couches ; suppression de la notion de clé ; liens 100 % manuels (flux / jointure, deux sens, colonne → colonne en option) avec file « À valider » et suppression ; suggestions en pointillés ; recherche globale (tag, base, table, colonne) et catalogue Tag → Base → Objets ; vue colonne et dictionnaire des colonnes ; journal partagé (dossier Teams), rafraîchissement automatique, versions enregistrées / comparées / restaurées, activité ; export Excel ; exécutable Windows et construction automatique par GitHub Actions ; ingestion SQL.

Reste : test à plusieurs postes sur le vrai dossier Teams ; signature de l'exe (si le support informatique l'exige) ; lineage multi-niveaux sur les vraies données.

## État d'avancement (2026-10-16) — version 0.4

Réalisé (plan : [11 — Plan v0.4](11_plan_v0.4.md)) :
- **Journal v2** : un fichier par version et par poste, regroupement automatique des anciens fichiers, Ctrl+Z / Ctrl+Y, annulation d'une ligne, retour à un point, vue de l'état passé ;
- **imports sans casse** : mode « Mettre à jour » qui garde les apports du SQL, liens orphelins signalés et jamais supprimés, tables `default.x` du SQL renommées quand le JSON les décrit ;
- **ordre des tags** = sens d'alimentation et colonnes du graphe de gauche à droite, couleurs, orientation automatique des flux ;
- **objets créés à la main** (New) : validation directe (confirmation) ou fusion avec leur définition SQL / JSON ;
- **analyseur SQL sqlglot** (CTE, sous-requêtes, UNION, CASE, Hive), liens SQL dans le graphe, jointures sans validation, règles de gestion sur les liens et étapes (CTE) ;
- **confort du graphe** : fenêtres qui ne se ferment pas en cliquant à côté, branches qui restent ouvertes, bouton retour et fil d'Ariane, zoom, mini-carte, recherche dans le graphe, navigation au clavier, panneaux repliables, raccourcis (Entrée, Suppr / E, F, G…) ;
- **vues** : vue d'ensemble par tag ou par base, vue 360, chaîne d'existence d'une colonne ;
- **suivi** des anomalies, modifications et améliorations, anomalies détectées automatiquement ;
- **script d'extraction Hive** (structure + SQL des vues), configuration réelle hors du dépôt.

Suite (phases 2 à 4) : données sensibles et leur empreinte en aval, glossaire métier, rôles légers, alertes de changement après import, tableau de bord qualité, vues enregistrées et liens partageables, export PNG / PDF du graphe, recherche dans les règles de gestion, guide de démarrage, workflow GitHub Actions, tests d'interface automatisés, hébergement interne.

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
