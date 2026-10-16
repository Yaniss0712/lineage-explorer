# Fiche de recette v0.4

À dérouler d'abord sur les **données fictives** de ce dossier, puis sur les vraies données au travail. Compter environ 45 minutes. Cocher chaque ligne ; noter ce qui ne va pas dans l'onglet **Suivi** de l'outil (type « Amélioration de l'outil »).

Préparation (données fictives) : **Importer → Exemples fictifs** → `architecture test/metadata_structure_seule.json`, puis `architecture test/requetes_architecture.sql`.

## 1. Tags et ordre d'alimentation

- [ ] **Tags** → créer `Objet métier` (1), `Lac` (2), `BV` (3), `Mart` (4), `Viz` (5). La ligne d'aperçu affiche `Objet métier → Lac → BV → Mart → Viz`.
- [ ] Règles de nommage : `lac_*` → Lac, `bv_*` → BV, `dm_*` → Mart.
- [ ] Ouvrir `dm_client.vue_360` : le graphe montre une colonne colorée par tag, **Lac à gauche, Mart à droite**.
- [ ] Modifier l'ordre d'un tag (ex. BV = 6) : la colonne BV passe à droite. Remettre 3.

## 2. Liens lus dans le SQL

- [ ] Rapport d'import du SQL : nombre de flux ajoutés (à valider) et de jointures (validées), requêtes comprises par l'analyseur complet.
- [ ] Dans le graphe, les flux SQL sont en tirets avec une pastille **SQL** ; les jointures en violet.
- [ ] Clic sur un flux SQL : le détail affiche les **règles de gestion de la requête** (jointures, filtres, formules, étapes CTE).
- [ ] Onglet **À valider** → « Tout valider » pour les liens SQL, puis Ctrl+Z sur l'un d'eux.
- [ ] Réimporter le même `.sql` : 0 nouveau lien, aucun doublon.
- [ ] Importer `requetes_complexes.sql` : 5 requêtes sur 5 comprises ; ouvrir `dm_risque.score_client` → onglet Règles : étapes `prets_actifs` et `retards`, condition `HAVING`.

## 3. Créer, valider et supprimer des liens

- [ ] Sélectionner `vue_360` → **Ajouter un lien** vers une table Lac, en mettant la table Lac en cible : le message indique que le sens sera inversé ; après création, le lien va bien du Lac vers le Mart.
- [ ] Clic sur un lien à valider dans le graphe → **Entrée** : il est validé.
- [ ] Clic sur un lien → **E** (ou **Suppr**) : il est supprimé ; **Ctrl+Z** le fait revenir ; **Ctrl+Y** le supprime de nouveau ; Ctrl+Z.
- [ ] Une jointure créée à la main est directement validée.
- [ ] Suggestions : boutons verts « Créer le lien » et rouges « Écarter ».

## 4. Objets créés à la main (New)

- [ ] **+ Objet** → `client` (type Objet métier, base `metier`, tag Objet métier).
- [ ] **+ Objet** → `tableau_risques` (Viz, base `powerbi`, tag Viz, colonnes `code_tiers_ref`).
- [ ] Les deux ont l'étiquette rouge **New** ; formes différentes (carré arrondi, rectangle).
- [ ] Liens : `client` → `lac_crm.clients_raw` ; `dm_client.vue_360` → `tableau_risques` (colonne `code_tiers_ref`).
- [ ] `client` → **Valider l'objet** → fenêtre de confirmation → Entrée : l'étiquette New disparaît.

## 5. Table prévue puis fusionnée avec son SQL

- [ ] Sélectionner `vue_360` → **+ Objet** → `score_appetence`, base `dm_client`, type Table, colonnes `code_tiers_ref`, `score`, cocher « relier à vue_360 (il est alimenté par lui) ».
- [ ] **Importer → Fusionner une table New** → coller :
  ```sql
  CREATE TABLE dm_client.score_appetence AS
  SELECT v.code_tiers_ref, CASE WHEN v.nb_contrats > 2 THEN 'FORT' ELSE 'FAIBLE' END AS score
  FROM dm_client.vue_360 v WHERE v.nb_comptes > 0
  ```
- [ ] **Analyser** : `score_appetence (New)` est proposé ; le menu déroulant liste les autres tables New.
- [ ] **Fusionner** : une seule table `score_appetence`, sans étiquette New, avec son lien et sa règle de gestion (filtre `nb_comptes > 0`, formule du score).

## 6. Vues

- [ ] Colonne `code_tiers_ref` → **pipeline** : chaîne d'existence `tableau_risques ← vue_360 ← tiers ← clients_raw ← client (niveau table)`.
- [ ] **Vue d'ensemble** : un bloc par tag, flèches épaissies selon le nombre de liens ; **Périmètre…** → Mart + Viz ; double-clic sur Mart : ses objets.
- [ ] Sur une table : **360°** (ou touche F) : tout l'amont et tout l'aval.
- [ ] **Retour arrière** (bouton ‹ ou touche Retour arrière) : on revient aux vues précédentes, branches ouvertes comprises ; le fil d'Ariane permet de sauter plusieurs étapes.

## 7. Confort du graphe

- [ ] Double-clic sur un objet ouvre ses voisins ; cliquer dans le vide, sélectionner un autre objet ou **Actualiser** : la branche reste ouverte.
- [ ] Une fenêtre (Tags, Importer…) ne se ferme pas en cliquant à côté ; × ou Échap la ferment.
- [ ] Zoom : boutons + / −, touches + − 0 C ; mini-carte cliquable.
- [ ] **G** : trouver `contrats` dans le graphe ; Entrée passe au résultat suivant.
- [ ] Flèches ← → : passer à l'objet en amont / en aval.
- [ ] Panneaux : chevrons sur les séparateurs, touches **[** et **]**.
- [ ] **?** : aide des raccourcis.

## 8. Suivi

- [ ] Sur `vue_360` → **Signaler** : anomalie « Encours négatif », priorité haute. Une pastille orange apparaît sur la bulle.
- [ ] Onglet **Suivi** : passer le sujet « en cours », assigné, avec un commentaire, puis « résolu » : la pastille disparaît.
- [ ] « + Signaler » sans objet, type « Amélioration de l'outil ».
- [ ] « Anomalies détectées automatiquement » : types incohérents, liens orphelins… → « Créer un sujet ».

## 9. Imports sans casse

- [ ] Réimporter `metadata_structure_seule.json` (mode Automatique = Mettre à jour) : tags, liens, objets créés, docs, suivi intacts ; les règles de gestion du SQL toujours là.
- [ ] Importer un JSON où une table a été retirée : ses liens deviennent orphelins (orange), rien n'est supprimé.

## 10. Retour dans le temps

- [ ] **Historique → Journal** : glisser le curseur ou « voir » sur une ligne ancienne : ce qui a changé depuis.
- [ ] « annuler » sur une ligne précise (elle devient barrée), puis « rétablir ».
- [ ] « Revenir à ce point » → confirmation : tout ce qui suit est annulé ; Ctrl+Z (ou « rétablir ») le fait revenir.
- [ ] **Enregistrer** une version : un nouveau fichier `journal\<version>__<nom>__<poste>.jsonl` apparaît dans Teams.

## Sur les vraies données (au travail)

- [ ] `tools\extract_hive_metadata.py` : copier `extraction.example.json` en `extraction.local.json`, renseigner le DSN et les bases, lancer.
- [ ] Importer le JSON produit, puis le `.sql` des vues (mode Compléter).
- [ ] Relever dans le rapport : requêtes comprises par l'analyseur complet / total, colonnes du SQL absentes de la structure.
- [ ] Deux personnes en même temps sur le dossier Teams : chacune voit les modifications de l'autre en moins d'une minute ; Ctrl+Z n'annule que ses propres modifications.
