# 11 — Plan v0.4 : ordre des tags, objets créés à la main, SQL qui comprend tout

> **État (2026-10-16) : les 7 lots sont réalisés**, 74 tests automatiques. Fiche de recette : [architecture test/RECETTE_v0.4.md](../architecture%20test/RECETTE_v0.4.md).

Ce plan regroupe les demandes de l'équipe après les premiers tests réels. Il est découpé en **7 lots**, chacun livré avec ses tests automatiques et une **fiche de recette** (scénario à dérouler à la main dans l'outil, comme pour l'ingestion SQL).

Ordre retenu : on commence par ce qui protège les données (journal, annulation, non-régression des imports), parce que tout le reste écrit dans le journal.

---

## Lot 0 — Fondations : journal, retour en arrière, imports sans casse

| Demande | Ce qui sera fait |
|---|---|
| Fusionner les petits fichiers du journal | Le journal devient **un fichier par version** : `journal/<version>.jsonl` (une ligne par modification). « Enregistrer la version » clôt le fichier courant et en ouvre un nouveau. Les anciens fichiers de 1 Ko sont regroupés automatiquement au premier lancement. Pour éviter les conflits OneDrive/Teams, chaque poste écrit dans son propre fichier de la version (`<version>.<utilisateur>.jsonl`) ; la relecture fusionne et trie par date. |
| Notion de temps, revenir à la modif d'avant | **Frise chronologique** dans Historique : on choisit un point (date ou modification) et l'outil affiche l'état de l'équipe à ce moment-là, en lecture seule. Bouton « Revenir à ce point » : annule toutes les modifications postérieures. |
| Supprimer un point du journal | Bouton « Annuler cette modification » sur chaque ligne de l'historique. Techniquement, un événement `event.revert` est ajouté : la modification disparaît de l'état, mais la trace (qui, quand) reste. Rien n'est effacé physiquement, pour qu'aucune synchro ne la fasse réapparaître. |
| Ctrl+Z et bouton retour | **Ctrl+Z** annule ma dernière modification (Ctrl+Y la refait). Bouton ↶ dans l'en-tête, avec l'intitulé de ce qui sera annulé (« Annuler : lien vue_360 → viz_risque »). |
| Un import JSON ou SQL ne doit rien casser | Règle stricte : un import ne touche **jamais** aux tags, liens, objets créés à la main, documentation, anomalies. Si une table disparaît du nouvel import, ses liens ne sont pas supprimés : ils passent en **« orphelin »** (signalés en orange, à revoir). |

**Tests** : `test_journal_v2.py` (regroupement des anciens fichiers, écriture concurrente de deux utilisateurs, relecture identique avant/après regroupement, annulation, retour à un point, Ctrl+Z/Ctrl+Y côté API) ; `test_import_non_regression.py` (on crée tags + liens + objets manuels + notes, on réimporte un JSON puis un SQL puis un JSON amputé d'une table : tout est conservé, les liens de la table disparue sont orphelins).

---

## Lot 1 — Tags à couches, couleurs, sens d'alimentation, objets créés à la main

### 1.1 L'ordre des tags

Pas de couches figées : l'équipe crée ses tags (`Objet métier`, `Lac`, `BV`, `Mart`, `Viz` ne sont que des exemples) et leur donne un **ordre**, déjà présent dans la fenêtre Tags. Chaque objet garde **un seul tag**.

- L'ordre définit le **sens d'alimentation** : un tag d'ordre plus petit alimente les tags d'ordre plus grand (Mart 4 alimente Viz 5).
- L'ordre définit la **place dans le graphe** : une colonne colorée par tag, de gauche à droite. Des tags de même ordre sont au même niveau ; un BV qui alimente un autre BV se place à sa droite, dans la même colonne de tag.

Conséquences :
- un lien créé entre un objet `Viz` et un objet `Mart` est **orienté automatiquement** Mart → Viz, quel que soit le sens du clic ;
- on peut garder un sens contraire à l'ordre (case « garder ce sens quand même ») : il est alors signalé « sens inhabituel » ;
- les suggestions utilisent les mêmes règles pour proposer le sens.

### 1.2 Couleurs

Chaque tag a une couleur (déjà le cas) ; chaque couche a une teinte de base. Les bulles du graphe, la liste de gauche et les cartes de recherche reprennent la couleur du tag. Légende repliable en bas du graphe.

### 1.3 Créer ses propres objets

Bouton **« + Objet »** dans le catalogue : nom, type (`Objet métier`, `Source`, `Table`, `Vue`, `Viz`, `Autre`), tag, description, colonnes facultatives (saisie rapide « une colonne par ligne »).

- Ces objets portent une étiquette **« New »** rouge dans un coin de la bulle, et dans la liste.
- Ils peuvent être **validés directement** (fenêtre de confirmation) : ils perdent l'étiquette New et restent tels quels, ou être **fusionnés** plus tard avec leur vraie définition (2.4).
- Ils se lient, se taguent et se documentent comme les autres.
- Usage typique : l'objet métier à la source de tout, la viz finale, une table prévue mais pas encore construite.

### 1.4 Graphe de gauche à droite

La disposition du graphe suit l'ordre des tags : **une colonne verticale par tag**, ce qui alimente à gauche, ce qui est alimenté à droite. Dans une même colonne, l'ordre vertical suit les liens pour limiter les croisements. Les objets sans tag se placent d'après leurs voisins (après leur source, avant leur cible).

**Tests** : `test_layers_objects.py` (orientation automatique d'un lien selon l'ordre des tags, sens gardé et signalé, jointure sans validation, colonnes du graphe dans l'ordre ; création, validation directe, doublons, suppression, recherche, survie à un réimport et à Ctrl+Z, outil utilisable sans aucun import ; fusion SQL et JSON d'une table New).

---

## Lot 2 — Le SQL qui comprend tout

C'est la valeur ajoutée principale : donner du code SQL et voir la cartographie se construire.

### 2.1 Analyseur SQL robuste

On inclut **sqlglot** (pur Python, licence MIT) dans le dossier portable, sans installation. L'analyseur actuel reste en secours si une requête n'est pas comprise.

Gains : sous-requêtes, `WITH` (CTE), `UNION`, `INSERT OVERWRITE … PARTITION`, `CASE`, fonctions de fenêtre, dialecte Hive/Spark, et un vrai **lineage de colonne** (quelle colonne source alimente quelle colonne cible, à travers les CTE).

### 2.2 Les trois étapes demandées

1. **Reconnaître les tables** : identifiant complet, puis nom seul s'il est unique, puis **tables « New »** de même nom (voir 2.4). Le rapport d'import liste reconnues / ajoutées / ambiguës.
2. **Reconnaître le sens** : `INSERT`, `CREATE TABLE AS`, `CREATE VIEW` → les tables lues alimentent la table créée. Un `SELECT` seul ne crée pas de sens, seulement des jointures.
3. **Reconnaître et afficher les règles de gestion** : déjà dans l'onglet Règles ; en plus, elles s'affichent **sur le lien** (clic sur une flèche : jointure, filtres, formules des colonnes de la requête qui l'a produit).

### 2.3 Le SQL modifie enfin le graphe

Problème constaté : après import SQL, rien ne change visuellement, parce que tout restait en suggestion.

Nouveau comportement :
- chaque flux lu dans le SQL crée un **lien « lu dans le SQL »**, visible tout de suite dans le graphe (trait plein fin, icône SQL), rattaché à sa requête ;
- les **jointures** lues dans le SQL sont créées directement, **sans validation** (c'est du code en production, pas une hypothèse) ;
- les flux restent validables en un clic si l'équipe veut les marquer « validé » ; on peut les supprimer comme les autres ;
- réimporter le même SQL ne crée pas de doublon (lien identifié par source + cible + requête).

### 2.4 Fusionner une table « New » avec sa vraie définition

Scénario : on crée à la main `table_x` (New) et ses liens ; plus tard la table est construite et on dispose de son `CREATE TABLE … AS SELECT …` ou d'un bout de JSON.

- Nouvel onglet **« Fusionner une table »** à côté d'Importer : on colle le SQL ou le JSON.
- L'outil propose la table New correspondante (même nom, ou colonnes proches) ; sinon **menu déroulant** des tables New.
- À la validation : la table New devient la vraie table (colonnes, type, règles de gestion), **ses liens et sa documentation sont conservés**, l'étiquette New disparaît, aucune table en double.
- Un import classique qui rencontre une table de même nom qu'une table New la propose aussi à la fusion dans le rapport, au lieu de créer un doublon.

**Tests** : `test_sql_v2.py` avec un jeu de requêtes réalistes et fictives (`architecture test/requetes_complexes.sql` : CTE, UNION, sous-requêtes, `INSERT OVERWRITE … PARTITION`, `CASE`, alias multiples) et une vérité terrain (`verite_terrain_sql.json`) : tables, sens, colonnes, règles attendues ; mesure du taux de reconnaissance. `test_sql_links.py` (liens créés, jointures sans validation, pas de doublon au réimport, suppression conservée au réimport). `test_merge_new.py` (fusion SQL et JSON, liens conservés, pas de doublon, menu des tables New).

---

## Lot 3 — Confort du graphe et raccourcis

| Demande | Ce qui sera fait |
|---|---|
| Les fenêtres ne doivent pas se fermer en cliquant ailleurs | Fenêtres **modales** : fermeture uniquement par ✕, « Annuler » ou Échap. Saisie en cours jamais perdue. |
| Ne pas refermer les dépendances ouvertes en cliquant ailleurs | Un clic dans le vide ne fait que désélectionner. Les branches ouvertes restent ouvertes. **Flèche retour** (et `Retour arrière`) pour revenir à l'état précédent du graphe, une étape à la fois. |
| Raccourcis sur un lien sélectionné | **Entrée** = valider, **Suppr** ou **E** = supprimer (avec confirmation courte annulable par Ctrl+Z). Aide des raccourcis avec `?`. |
| Suggestions | Boutons **vert « Créer le lien »** et **rouge « Écarter »**, dans la liste et sur la flèche en pointillé. |
| Mieux zoomer | Boutons +, −, « Ajuster », zoom sur la sélection, mini-carte en bas à droite pour les grands graphes. |
| Fermer à gauche et à droite | Panneaux latéraux **repliables** (chevron et raccourcis `[` et `]`), le graphe prend toute la largeur. |

**Tests** : tests d'interface pilotés par navigateur (`tests/ui/`) : modale qui reste ouverte, raccourcis Entrée/Suppr/E, retour arrière, panneaux repliés ; plus la fiche de recette.

---

## Lot 4 — Vues d'ensemble

### 4.1 Big picture d'un périmètre

Nouvelle vue **« Périmètre »** : on choisit un ou plusieurs tags (ou couches, ou bases). Le graphe montre **un bloc par tag**, rangé par couche de gauche à droite, avec le nombre d'objets et l'épaisseur des flèches selon le nombre de liens. Un clic sur un bloc l'ouvre en objets.

### 4.2 Focus 360 sur une table

Dans une base, on choisit une table : **tout l'amont et tout l'aval**, couche par couche, jusqu'à l'objet métier d'origine et aux viz finales. Profondeur réglable, filtres « liens validés seulement » / « avec SQL ».

### 4.3 Pipeline d'une colonne

Au clic sur une colonne : les objets qui la contiennent (déjà fait) **et sa chaîne d'existence** :

> viz_x ← mart_y.`montant` ← bv_z.`montant_crd` ← lac.`mt_crd` ← objet métier « Prêt »

La chaîne utilise les correspondances de colonnes des liens et le lineage colonne du SQL ; quand un maillon n'est connu qu'au niveau table, il apparaît « niveau table » en gris. But : retracer l'origine de chaque table et de chaque colonne.

**Tests** : `test_views.py` (agrégation par tag, focus 360 sur un jeu à 5 couches, chaîne de colonne avec maillons SQL, liens et niveau table).

---

## Lot 5 — Suivi des anomalies, modifications et améliorations

Un **registre** partagé (dans le journal, donc visible de tous) :

- **Types** : anomalie de données, demande de modification, amélioration de l'outil.
- **Rattachement** : à un objet, une colonne, un lien, ou à rien (pour l'outil).
- **Champs** : titre, description, priorité, statut (`Ouvert → En cours → Résolu / Rejeté`), responsable, commentaires datés.
- **Affichage** : pastille sur les bulles concernées, onglet « Suivi » dans le détail, liste filtrable à gauche, export Excel.
- Les **anomalies détectées automatiquement** (types incohérents entre tables, colonnes du SQL absentes de la structure, liens orphelins, sens inhabituel) peuvent être transformées en ticket en un clic.

**Tests** : `test_tracking.py` (cycle de vie complet, rattachements, filtres, export, ticket depuis une anomalie détectée).

---

## Lot 6 — Extraction automatique et suite de la feuille de route

### 6.1 Script d'extraction Hive

Ton script sert de base. Version intégrée au dépôt : `tools/extract_hive_metadata.py`.

- **Ce qui ne va pas sur GitHub** : le nom du DSN et la liste des bases. Ils sont lus dans un fichier local `extraction.local.json` (ignoré par git, vérifié par `check_no_data.py`) ; le dépôt contient seulement `extraction.example.json` fictif.
- **Ajouts** :
  - `SHOW CREATE TABLE` pour les vues → un fichier `.sql` à côté du JSON, importable directement en mode Compléter (les règles de gestion et les liens des vues arrivent tout seuls) ;
  - commentaires de table et de colonne → descriptions ;
  - colonnes de partition marquées comme telles ;
  - `is_key` n'est plus produit (ignoré par l'outil) ;
  - reprise sur erreur et journal des erreurs (déjà présent, conservé).
- **Rafraîchissement** : on relance le script, on importe : le rapport montre ce qui a changé, rien n'est cassé (lot 0).

### 6.2 Feuille de route des 4 phases (rappel, mise à jour)

| Phase | Contenu | État |
|---|---|---|
| 1. Fiabiliser sur les vraies données | Test réel à 2–3 personnes ; analyseur SQL robuste ; extraction automatique ; compaction du journal | Test réel : **en cours, OK**. SQL, extraction, journal : lots 2, 6 et 0 |
| 2. Gouvernance et valeur métier | Données sensibles et leur empreinte en aval ; glossaire métier ; rôles légers (qui valide) ; alertes de changement après import ; tableau de bord qualité | Après les lots 0 à 6 |
| 3. Confort | Vues enregistrées et liens partageables ; export PNG/PDF du graphe ; recherche dans les règles de gestion ; guide de démarrage | Après la phase 2 (une partie est couverte par les lots 3 et 4) |
| 4. Industrialisation | Workflow GitHub Actions ; tests d'interface automatisés (démarrés au lot 3) ; question au support informatique sur OneDrive et un hébergement interne | En parallèle |

---

## Plan de test global (recette à faire au travail)

Une fiche `architecture test/RECETTE_v0.4.md` déroule ces scénarios sur les données fictives, puis sur les vraies :

1. **Liens** : en créer à la main (table → table, colonne → colonne), en créer par un import SQL, les valider (Entrée), les supprimer (Suppr / E), annuler (Ctrl+Z).
2. **Objets** : ajouter un objet métier source, une viz finale, une table New ; les taguer ; vérifier couleurs et étiquette New.
3. **Liens avec ces objets** : objet métier → lac → BV → mart → viz ; vérifier que le graphe se range de gauche à droite et que le sens est corrigé tout seul.
4. **Règles de gestion** : importer le SQL de la table New via « Fusionner une table » ; vérifier qu'il n'y a pas de doublon, que les liens sont gardés, que les règles s'affichent sur la table et sur le lien.
5. **Suivi** : déclarer une anomalie sur une colonne, une demande de modification sur une table, une amélioration de l'outil ; les faire évoluer.
6. **Non-régression** : réimporter le JSON complet, puis un SQL ; vérifier que tout le travail précédent est intact.
7. **Retour dans le temps** : afficher l'état d'hier, annuler une modification précise depuis l'historique.

Les tests automatiques passent de 38 à environ 90.

---

## Points à trancher avant de commencer

1. **Liens issus du SQL** : je propose de les créer directement (statut « lu dans le SQL », visibles dans le graphe) et de créer les **jointures sans validation**. Les flux restent validables. D'accord ?
2. ~~Couches~~ : tranché, pas de couches, l'ordre des tags suffit (un tag par objet).
3. **Échap** ferme-t-il les fenêtres, ou seulement ✕ et Annuler ?
4. **Ordre des lots** : 0 → 1 → 2 → 3 → 4 → 5 → 6, ou le lot 2 (SQL) avant le lot 1 pour la démo ?
