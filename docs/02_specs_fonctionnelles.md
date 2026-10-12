# 02 — Spécifications fonctionnelles

## 1. Glossaire

| Terme | Définition |
|---|---|
| **Objet** | Une table ou une vue, identifiée par `base.nom` |
| **Tag** | Catégorie définie par l'équipe (ex. Source, Fiche client, Restitution). Un tag par objet |
| **Règle de nommage** | Motif qui attribue un tag automatiquement (`tracking_*` → « Tracking web ») |
| **Lien – flux** | Orienté : A alimente B (B est alimentée par A). Option colonne → colonne |
| **Lien – jointure** | Non orienté : A et B se relient (par une ou plusieurs colonnes) |
| **Suggestion** | Lien probable proposé par l'outil (colonnes communes, SQL importé). Jamais créé automatiquement |
| **À valider / validé** | Statut d'un lien créé par un humain ; l'auteur peut valider lui-même |
| **Version** | Instantané de l'état de l'équipe, enregistré par le bouton « Enregistrer la version » |
| **Espace partagé** | Dossier Teams contenant l'exe, les imports, le journal et les versions |

## 2. User stories (toutes réalisées)

| ID | Je veux… | Réalisation |
|---|---|---|
| US-01 | voir toutes les tables sans chercher | Catalogue **Tag → Base → Objets**, replié, avec compteurs |
| US-02 | chercher par nom de tag, de base, de table ou de colonne, sur tout le catalogue | Recherche globale, préfixes `tag:` `base:` `table:` `col:` combinables |
| US-03 | créer mes propres tags, plus fins que Lac / BV / Mart | Gestionnaire de tags (nom, couleur, ordre) + règles de nommage + tag en masse |
| US-04 | voir toutes les tables d'une colonne (ex. `code_tiers`) | **Vue colonne** ; types différents signalés ; noms proches en option |
| US-05 | créer un lien dans un sens ou dans l'autre | Éditeur : « alimente / est alimentée par », bouton ⇄, flux ou jointure |
| US-06 | relier des colonnes (facultatif) | Paires colonne → colonne avec transformation |
| US-07 | garder les suggestions sans qu'elles deviennent des liens | Suggestions en pointillés + onglet dédié ; « Créer le lien… » / « Écarter » |
| US-08 | qu'un lien créé passe par « à valider » et puisse être supprimé | File « À valider », validation par l'auteur possible, suppression tracée |
| US-09 | documenter (description, propriétaire, URL de spec, notes) | Onglet Doc du détail |
| US-10 | que toute l'équipe voie les modifications | Journal partagé, relu toutes les 15 s, message « N modifications de … » |
| US-11 | enregistrer la version actuelle | Bouton dédié ; historique, comparaison, restauration |
| US-12 | ouvrir l'outil d'un double-clic | `LineageExplorer.cmd` dans le dossier Teams (Python portable officiel inclus) |
| US-13 | voir amont / aval, l'impact, le chemin entre deux tables | Graphe, export CSV d'impact, recherche de chemin |
| US-14 | exporter la documentation | Export Excel (objets, liens, colonnes liées, tags, règles) |
| US-15 | réimporter une nouvelle extraction sans rien perdre | Tags et liens conservés ; objets / colonnes apparus et disparus signalés ; liens « à revoir » |

## 3. Écran principal

```
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ ◉ Lineage   [ Rechercher table, colonne, tag, base… ]   à jour · Tags · Historique ·         │
│                                                         [Enregistrer la version] [Importer] ⋯ │
├──────────────────────┬──────────────────────────────────────────────┬────────────────────────┤
│ Catalogue│À valider  │      [Amont|Tout|Aval]  − 2 +  ⚙  ◎  ⤢        │ ● Fiche client  TABLE  │
│ Suggestions│Colonnes │                                              │ client_fiche_identite  │
│ ▾ ■ Fiche client  8  │   (src) ──▶ (table) ──▶ (cible)               │ Tag [Fiche client ▾]   │
│   ▾ db_prod  8       │      ┈┈┈ suggestion   - - - à valider          │ [Ajouter un lien] …    │
│     fiche_client_…   │                                              │ Colonnes│Liens│Sugg.│Doc│
│ ▸ ■ Tracking web 6   │                                              │                        │
│ ▸ ■ Sans tag   20    │                                              │                        │
└──────────────────────┴──────────────────────────────────────────────┴────────────────────────┘
```

## 4. Codes visuels

| Élément | Règle |
|---|---|
| Couleur d'une bulle | Couleur du tag (gris = sans tag) ; bulle pleine = objet central |
| Chiffre dans la bulle | Nombre de colonnes |
| Point bleu | Une URL de spec est renseignée |
| Trait plein + flèche | Flux validé |
| Tirets orange | Lien à valider |
| Pointillés gris | Suggestion (flèche si un sens est proposé) |
| Trait violet | Jointure |
| Rouge (vue colonne) | Type différent de la majorité |

## 5. Interactions

| Geste | Effet |
|---|---|
| Clic sur une bulle | Détail de l'objet |
| Double-clic | Étendre les voisins (vue colonne : ouvrir la table) |
| Clic droit | Masquer la bulle |
| Clic sur un lien / une suggestion | Détail, valider / modifier / supprimer, ou créer le lien |
| Molette, glisser | Zoom, déplacement |
