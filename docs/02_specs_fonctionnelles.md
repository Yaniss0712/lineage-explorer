# 02 — Spécifications fonctionnelles

## 1. Glossaire

| Terme | Définition |
|---|---|
| **Lac** | Couche d'ingestion brute (données sources, peu transformées) |
| **BV** | Couche intermédiaire de vues/tables construites à partir du lac |
| **Mart** | Couche de restitution orientée métier (alimente le reporting) |
| **Objet** | Une table ou une vue, identifiée par `base.nom` |
| **Colonne** | Un champ d'un objet, identifié par `base.nom.colonne` |
| **Flux** (lineage) | Lien **orienté** : l'objet A alimente l'objet B (éventuellement colonne à colonne) |
| **Relation** (clé) | Lien **non orienté** : A et B partagent une clé de jointure |
| **Lien suggéré** | Lien détecté automatiquement, avec un score de confiance, à valider |
| **Annotation** | Information ajoutée à la main par l'équipe : URL de spec, lien manuel, validation/rejet |
| **Bulle** | Représentation d'un objet dans le graphe |

## 2. User stories

Priorisation **MoSCoW** : M = Must (MVP), S = Should, C = Could, W = Won't (pour l'instant).

### Recherche

| ID | En tant que… | je veux… | afin de… | Prio |
|---|---|---|---|---|
| US-01 | utilisateur | chercher un nom de table ou de colonne (recherche partielle, insensible à la casse et aux accents) | retrouver une donnée sans connaître son emplacement | M |
| US-02 | utilisateur | filtrer les résultats par couche (Lac / BV / Mart) et par base | réduire le bruit | M |
| US-03 | utilisateur | chercher aussi dans les descriptions | trouver une donnée par son sens métier | S |
| US-04 | utilisateur | chercher par type de colonne (ex. `decimal`) | auditer des formats | C |

### Exploration (bulles)

| ID | En tant que… | je veux… | afin de… | Prio |
|---|---|---|---|---|
| US-10 | utilisateur | ouvrir une bulle pour voir ses colonnes, ses types, ses liens et sa spec | comprendre l'objet | M |
| US-11 | utilisateur | afficher les voisins d'une bulle (amont, aval, relations) sur N niveaux | explorer progressivement | M |
| US-12 | utilisateur | distinguer visuellement les couches et les types de liens | lire le graphe d'un coup d'œil | M |
| US-13 | utilisateur | replier / masquer une partie du graphe | garder une vue lisible | S |
| US-14 | utilisateur | trouver le chemin entre deux tables | savoir comment les joindre | S |

### Lineage

| ID | En tant que… | je veux… | afin de… | Prio |
|---|---|---|---|---|
| US-20 | utilisateur | voir le lineage **amont** d'un objet (jusqu'au lac) | savoir d'où viennent les données | M |
| US-21 | utilisateur | voir le lineage **aval** d'un objet (jusqu'aux marts) | mesurer l'impact d'une modification | M |
| US-22 | utilisateur | voir le lineage d'une **colonne** avec ses transformations | comprendre un calcul | S |
| US-23 | utilisateur | exporter la liste des objets impactés (CSV) | la partager dans une analyse d'impact | C |
| US-24 | utilisateur | voir **pourquoi** un flux inféré est proposé (colonnes communes, clé, score) | décider de le valider ou non | M |
| US-25 | utilisateur | traiter une **file de revue** des flux inférés, triée par impact | fiabiliser le lineage rapidement | M |
| US-26 | utilisateur | régler un **seuil de confiance** minimal dans le graphe | masquer les flux trop incertains | S |
| US-27 | utilisateur | voir les **colonnes non expliquées** d'un objet (renommages, calculs) et les relier à la main | compléter le lineage colonne | S |

### Annotations (partagées)

| ID | En tant que… | je veux… | afin de… | Prio |
|---|---|---|---|---|
| US-30 | utilisateur | ajouter l'**URL de spec Confluence** sur une bulle | centraliser la documentation | M |
| US-31 | utilisateur | **valider ou rejeter** un lien suggéré | fiabiliser le graphe | M |
| US-32 | utilisateur | **ajouter un lien manuel** (relation ou flux) entre deux objets | compléter ce que l'extraction ne voit pas | M |
| US-33 | utilisateur | voir qui a fait l'annotation et quand | faire confiance à l'information | S |
| US-34 | utilisateur | ajouter une note libre sur un objet | partager une connaissance terrain | C |

## 3. Écrans

### 3.1 Écran principal

```
┌───────────────────────────────────────────────────────────────────────┐
│ 🔍 [ Rechercher une table, une colonne…        ]  [Lac][BV][Mart] ⚙  │
├───────────────────┬───────────────────────────────────┬───────────────┤
│ RÉSULTATS         │              GRAPHE               │ DÉTAIL        │
│                   │                                   │               │
│ ▸ bv_client.tiers │      (lac)──▶(bv)──▶(mart)        │ bv_client     │
│   · code_tiers    │        │      ╎                   │   .tiers      │
│ ▸ dm_pret.encours │      (lac)  (bv)                  │ Couche : BV   │
│   · code_tiers_ref│                                   │ Spec : 🔗     │
│                   │  Profondeur [2]  ⬆ Amont ⬇ Aval   │ Colonnes (24) │
│                   │  ↔ Relations   ⟳ Recentrer        │ Liens (7)     │
└───────────────────┴───────────────────────────────────┴───────────────┘
```

### 3.2 Panneau de détail (bulle ouverte)

- **En-tête** : nom, couche, base, type (table/vue), propriétaire, dernière extraction.
- **Spec** : URL Confluence cliquable + bouton ✏️ pour la modifier.
- **Colonnes** : nom, type, clé (🔑), description ; clic sur une colonne → lineage de la colonne.
- **Flux** : liste amont / aval avec job éventuel.
- **Relations** : liste des liens de clés avec statut (✅ validé, ❔ suggéré + score, ❌ rejeté, ✍️ manuel).
- **Actions** : ajouter un lien, valider/rejeter, ajouter une note.

### 3.3 Fenêtre « Ajouter un lien »

- Type : **Relation** (clé) ou **Flux** (lineage).
- Objet cible : champ avec autocomplétion.
- Colonnes source / cible (facultatif).
- Commentaire (facultatif).
- Auteur et date renseignés automatiquement.

## 4. Codes visuels des bulles

| Élément | Règle |
|---|---|
| Couleur | Une couleur par couche : Lac / BV / Mart |
| Taille | Proportionnelle au nombre de colonnes (bornée) |
| Bordure épaisse | Objet sélectionné / centre de la vue |
| Badge 🔗 | Une URL de spec est renseignée |
| Flèche pleine | Flux de lineage certain (manuel, déclaré, SQL) ou validé |
| Flèche pointillée | Flux **inféré**, non encore validé — épaisseur selon le score |
| Trait pointillé | Relation de clé suggérée |
| Trait plein sans flèche | Relation de clé validée ou déclarée |

## 5. Interactions

| Geste | Effet |
|---|---|
| Clic sur une bulle | Ouvre le panneau de détail |
| Double clic | Étend les voisins d'un niveau |
| Clic sur un lien | Affiche les colonnes concernées et le statut |
| Boutons ⬆ / ⬇ | Lineage amont / aval à partir de la bulle sélectionnée |
| Molette / pincement | Zoom |
| Bouton ⟳ | Recentre la vue sur l'objet sélectionné |

## 6. Règles liées au très grand volume

- **Le graphe complet n'est jamais affiché.** On part toujours d'un objet (recherche ou sélection).
- Profondeur par défaut : **2 niveaux**, maximum réglable.
- Plafond d'affichage : **150 bulles** ; au-delà, l'outil affiche « +N objets non affichés » et propose de filtrer.
- La recherche renvoie au maximum **50 résultats** par page.

## 7. Critères d'acceptation du MVP

- [ ] Une recherche sur un nom de colonne renvoie les résultats en **< 1 s** sur le volume réel.
- [ ] Le lineage amont/aval d'une table s'affiche en **< 2 s**.
- [ ] À partir d'un JSON de **structure seule**, l'outil propose des flux avec une **précision ≥ 80 %** sur la vérité terrain fournie par les data engineers.
- [ ] Chaque flux inféré affiche son explication et son score.
- [ ] Une URL de spec ajoutée par un utilisateur est visible par un autre après rafraîchissement.
- [ ] Un lien rejeté ne réapparaît pas après une nouvelle extraction.
- [ ] L'outil fonctionne **sans connexion internet** et sans paquet installé hors Python.
