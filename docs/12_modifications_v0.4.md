# 12 — Modifications du 16/10/2026 : de la v0.3 à la v0.4

Toutes les modifications de la journée, entre la version 0.3 (celle déployée sur Teams) et la version 0.4. Plan d'origine : [11 — Plan v0.4](11_plan_v0.4.md). Fiche de recette : [architecture test/RECETTE_v0.4.md](../architecture%20test/RECETTE_v0.4.md).

## Fonctionnalités

| # | Domaine | Avant (v0.3) | Maintenant (v0.4) | Où dans l'outil |
|---|---|---|---|---|
| 1 | Journal | Un petit fichier `.json` par modification (des milliers à terme) | Un fichier `.jsonl` par version et par poste, une ligne par modification ; « Enregistrer » ouvre un nouveau fichier | `journal\` |
| 2 | Journal | — | Regroupement automatique des anciens fichiers en un seul `archive__….jsonl`, avec sauvegarde zip, relecture et comparaison de l'état avant suppression | Premier lancement, ou `FusionnerJournal.cmd` |
| 3 | Annuler | Impossible | **Ctrl+Z / Ctrl+Y** et boutons ↶ ↷ (n'annule que ses propres modifications) | En-tête, clavier |
| 4 | Historique | Liste d'activité en lecture seule | Annuler / rétablir une ligne précise, voir l'état à un moment donné (curseur), **revenir à ce point** ; rien n'est jamais effacé | Historique → Journal |
| 5 | Imports | Un JSON réimporté remplaçait la structure (règles SQL perdues) | Mode **Mettre à jour** (défaut pour un JSON) : la structure change, les apports du SQL sont conservés | Importer |
| 6 | Imports | Table disparue : liens signalés « à revoir » | Liens **orphelins** (orange), jamais supprimés, listés dans les anomalies | Graphe, Suivi |
| 7 | Imports | Table `default.x` du SQL et `base.x` du JSON : deux objets | Renommage automatique, les liens suivent | Rapport d'import |
| 8 | Tags | L'ordre servait seulement à proposer le sens des suggestions | L'ordre = **sens d'alimentation** : un flux est orienté automatiquement (case « garder ce sens » possible, alors signalé) | Tags, Ajouter un lien |
| 9 | Graphe | Disposition par profondeur | **Une colonne colorée par tag**, de gauche à droite selon l'ordre, avec son nom et son nombre d'objets | Graphe |
| 10 | Objets | Seuls les objets du JSON / SQL | **+ Objet** : objet métier, source, table, vue, viz, autre ; colonnes facultatives ; étiquette rouge **New** ; formes différentes | Catalogue, touche N |
| 11 | Objets | — | **Valider l'objet** directement (fenêtre de confirmation) : l'étiquette New disparaît | Détail d'un objet New |
| 12 | Objets | — | **Fusionner une table New** avec son vrai `CREATE TABLE … AS SELECT` (ou un bout de JSON) : proposition automatique, menu des tables New, liens / tag / doc / notes / suivi conservés, aucun doublon | Importer → Fusionner une table New |
| 13 | Objets | — | Un import qui contient une table de même nom qu'une table New propose la fusion | Rapport d'import, Suivi |
| 14 | SQL | Expressions régulières (CTE, UNION, sous-requêtes mal lues) | **sqlglot** inclus (pur Python, MIT) : CTE, sous-requêtes, UNION, CASE, fenêtres, `INSERT OVERWRITE … PARTITION`, `USING`, `IN (SELECT …)` ; ancien analyseur en secours | Import SQL |
| 15 | SQL | Le SQL ne créait que des suggestions : rien ne changeait dans le graphe | Flux lus dans le SQL = **liens visibles** (à valider, pastille SQL) ; jointures = liens **déjà validés** ; pas de doublon au réimport ; un lien supprimé n'est pas recréé | Graphe, À valider |
| 16 | SQL | Règles de gestion dans le détail d'une table | Aussi **sur le lien** ; étapes des CTE (filtres, regroupements) ; formules qui traversent les CTE | Clic sur un lien, onglet Règles |
| 17 | Liens | Toute jointure à valider | Une jointure est validée dès sa création ; « Tout valider » pour les liens SQL | À valider |
| 18 | Raccourcis | Échap seulement | **Entrée** valider, **Suppr / E** supprimer, Ctrl+Z / Y, flèches pour naviguer, Retour arrière, F (360°), G (trouver), + − 0 C (zoom), / (recherche), [ ] (panneaux), N (objet), ? (aide) | Partout |
| 19 | Fenêtres | Se fermaient en cliquant à côté ; `confirm()` du navigateur | Ne se ferment qu'avec × ou Échap ; confirmations dans le style de l'outil (Entrée = confirmer) | Toutes les fenêtres |
| 20 | Graphe | Une actualisation ou un lien créé refermait les branches ouvertes | Branches ouvertes, objets masqués, zoom et sélection **conservés** ; clic dans le vide = simple désélection | Graphe |
| 21 | Navigation | — | **Bouton retour** et **fil d'Ariane** (objets, colonne, vue d'ensemble, chemin) | Haut du graphe |
| 22 | Repérage | Molette seulement | Boutons de zoom et niveau en %, **mini-carte** cliquable, **trouver dans le graphe**, survol qui met en avant les voisins, légende des tags | Graphe |
| 23 | Panneaux | Largeur réglable | Panneaux gauche / droit **repliables** (chevrons, touches [ et ]) | Séparateurs |
| 24 | Suggestions | Boutons « Créer le lien… » / « Écarter » neutres | Bouton **vert** « Créer le lien », bouton **rouge** « Écarter » | Suggestions |
| 25 | Vues | — | **Vue d'ensemble** d'un périmètre : un bloc par tag (ou par base), flèches épaissies selon le nombre de liens, choix des tags, ouverture des objets d'un bloc | En-tête |
| 26 | Vues | — | **Vue 360°** d'une table : tout l'amont et tout l'aval | Barre du graphe, détail, touche F |
| 27 | Colonne | Les tables qui portent la colonne, disposées en cercle | Tables rangées par tag + **chaîne d'existence** : viz ← mart ← BV ← lac ← objet métier (colonne, même nom ou niveau table) | Bouton « pipeline » d'une colonne |
| 28 | Suivi | — | Onglet **Suivi** : anomalies, demandes de modification, améliorations de l'outil ; statut, priorité, responsable, commentaires, historique ; pastille orange sur les bulles | Onglet Suivi, « Signaler » |
| 29 | Suivi | — | **Anomalies détectées** automatiquement : types incohérents, liens orphelins, sens inverse de l'ordre, colonnes du SQL absentes, tables New devenues réelles | Onglet Suivi |
| 30 | Recherche | « tableau de bord » ne trouvait pas `tableau_de_bord` | Espaces = soulignés | Recherche |
| 31 | Export | 5 onglets Excel | + onglets **Objets créés** et **Suivi** | ⋯ → Exporter |
| 32 | Extraction | Script manuel hors de l'outil | `tools/extract_hive_metadata.py` : structure JSON + **SQL des vues** (`SHOW CREATE TABLE`) + commentaires + colonnes de partition + erreurs ; DSN et bases dans `extraction.local.json` (jamais sur GitHub) | `tools\` |
| 33 | Déploiement | `app\`, `LineageExplorer.cmd` | + `app\vendor\sqlglot\`, `FusionnerJournal.cmd`, LISEZMOI mis à jour | Dossier Teams |

## Technique

| Fichier | Modification |
|---|---|
| `engine/workspace.py` | Journal `.jsonl` (lecture par taille / date, lignes partielles ignorées), regroupement sécurisé, `event.revert` / `event.restore`, annuler / rétablir, état à un point, objets créés (`object.*`), fusion (`object.merge`, `objects.rename`), `links.import`, suivi (`ticket.*`) |
| `engine/server.py` | Objets créés intégrés au cache, ordre des tags (`tag_order`, `lanes`), orientation des liens, imports `merge` / `refresh` / `replace`, liens SQL, fusion (`merge/preview`, `merge/apply`), `link/<id>`, `overview`, `anomalies`, `tickets`, `history/*`, chaîne d'existence d'une colonne |
| `engine/sql_glot.py` (nouveau) | Analyse sqlglot : sources, cible, lineage de colonne à travers CTE et UNION, jointures, règles lisibles |
| `engine/sql_ingest.py` | sqlglot d'abord, expressions régulières en secours ; compteur `parsers` |
| `engine/ingest.py` | Correspondance des identifiants renvoyée par la fusion, étapes des règles |
| `engine/__main__.py` | Commande `compact` (`--dry-run`) |
| `web/graph.js` | Réécrit : colonnes par tag, formes et badges, vue d'ensemble, mini-carte, zoom, survol, instantanés pour le retour |
| `web/app.js` | Réécrit : navigation, raccourcis, objets New, fusion, historique, vue d'ensemble, suivi, chaîne d'existence |
| `web/index.html`, `web/style.css` | Nouvelle barre (annuler, vue d'ensemble), onglet Suivi, zoom, mini-carte, panneaux repliables |
| `vendor/sqlglot/` (nouveau) | sqlglot 30.23.0 (MIT) |
| `tools/extract_hive_metadata.py`, `tools/extraction.example.json` (nouveaux) | Extraction Hive |
| `tools/build_portable.py`, `tools/check_no_data.py`, `.gitignore` | `vendor`, `FusionnerJournal.cmd` ; refus de `extraction.local.json` |

## Tests

| Fichier | Ce qui est vérifié |
|---|---|
| `tests/test_journal_v2.py` (nouveau) | Un fichier par poste, copies « conflit », lignes partielles, regroupement + sauvegarde + simulation, commande `compact`, Ctrl+Z / Ctrl+Y, annuler une ligne, revenir à un point |
| `tests/test_import_non_regression.py` (nouveau) | JSON → SQL → JSON sans rien perdre, liens orphelins, mode Remplacer, renommage `default.x` → `base.x` |
| `tests/test_layers_objects.py` (nouveau) | Orientation par l'ordre des tags, jointure validée, colonnes du graphe ; objets New (création, validation, doublons, suppression, Ctrl+Z, sans import) ; fusion SQL et JSON |
| `tests/test_sql_v2.py` (nouveau) | `requetes_complexes.sql` contre `verite_terrain_sql.json` : 100 % des flux, colonnes et jointures (ancien analyseur : 5/8 flux, 4/12 colonnes, 0/3 jointures) |
| `tests/test_views_tracking.py` (nouveau) | Vue d'ensemble, chaîne d'existence jusqu'à l'objet métier, retour dans le temps, suivi, export Excel, anomalies |
| `tests/test_extract.py` (nouveau) | Extraction avec un faux curseur ODBC ; aucune configuration réelle dans le dépôt |
| `tests/test_sql_ingest.py`, `tests/test_merge_sql.py` | Adaptés : le SQL crée désormais des liens |

Total : **75 tests** (38 en v0.3). Vérification supplémentaire : un journal de 137 fichiers écrit par le code réel de la v0.3 est regroupé en un fichier, avec un état de l'équipe identique.
