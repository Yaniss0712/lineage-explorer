# 01 — Vision du projet

## 1. Le problème

L'architecture data est **immense** : des lacs, des BV, des marts, des milliers de tables et des centaines de milliers de colonnes. Au quotidien, cela se traduit par des questions qui prennent des heures :

- **« Où est cette donnée ? »** — Dans quelle table trouve-t-on le code tiers, le montant d'un prêt, la catégorie client ?
- **« D'où vient ce champ ? »** — Le champ du mart est-il calculé, renommé, filtré ? Depuis quelle table du lac ?
- **« Qu'est-ce que je casse si je modifie ça ? »** — Quels BV et marts dépendent de cette table du lac ?
- **« Comment je relie ces deux tables ? »** — Par quelle clé, et via quelles tables intermédiaires ?
- **« Où est la spec ? »** — La documentation existe sur Confluence, mais personne ne sait où.

Aujourd'hui, la réponse dépend de la mémoire de quelques personnes et de la lecture manuelle du code.

## 2. La solution

Un **outil de lineage léger, local et partagé** :

1. Un **script d'extraction** (Python, VS Code) produit un JSON décrivant la structure : objets, colonnes, types, flux d'alimentation.
2. Un **moteur Python** ingère ce JSON, **détecte les liens** (clés communes) et **infère le lineage** par algorithmes (colonnes communes, rareté, ordre des couches), jusqu'à la colonne. Chaque flux inféré porte un **score** et une **explication** ; l'équipe le valide ou le rejette.
3. Une **interface en bulles** permet de **chercher**, d'**explorer** le voisinage d'une table et d'**annoter** (URL de spec Confluence, liens manuels, validation des liens suggérés).

## 3. Objectifs

| # | Objectif | Mesure de succès |
|---|---|---|
| O1 | Trouver une donnée rapidement | Une colonne retrouvée en **< 10 secondes** par recherche |
| O2 | Tracer l'origine d'un champ | Lineage amont d'une colonne de mart affiché en **≤ 3 clics** |
| O3 | Mesurer l'impact d'une modification | Liste des objets aval d'une table du lac en **1 clic** |
| O4 | Centraliser la connaissance | Chaque table peut porter une **URL de spec** et des liens manuels, visibles par toute l'équipe |
| O5 | Fonctionner sur un poste contraint (sans internet ni droits d'installation) | Aucune installation hors Python, **aucune IA**, aucun appel externe |

## 4. Utilisateurs

| Profil | Usage principal |
|---|---|
| **Data engineers** | Analyse d'impact, compréhension des flux, debug |
| **Business analysts** | Retrouver une donnée, comprendre sa définition, accéder à la spec |
| **Nouveaux arrivants** | Découvrir l'architecture sans dépendre de la mémoire des autres |
| **PO / responsables** | Vision d'ensemble, documentation, démonstration de la valeur |

## 5. Périmètre

### Inclus (MVP)

- Ingestion d'un JSON de structure (Lac / BV / Mart, tables, colonnes, types).
- Lineage **niveau table** (quel objet alimente quel objet).
- Détection automatique des **liens de clés** (nom identique, nom partiel, lien déclaré).
- Recherche globale (tables, colonnes, descriptions).
- Vue graphe focalisée en bulles, avec panneau de détail.
- Annotations partagées : URL de spec, liens manuels, validation/rejet des liens suggérés.

### Inclus (après MVP)

- Lineage **niveau colonne** avec transformation affichée.
- Recherche de chemin entre deux tables.
- Export (CSV / PNG) d'une vue.

### Exclus

- Toute IA ou suggestion par LLM dans l'outil livré.
- Connexion directe aux bases en production depuis l'outil (l'extraction reste un script séparé).
- Lecture du contenu des données (on ne manipule que la **structure**).
- Synchronisation automatique avec Confluence (on stocke **uniquement l'URL**).

## 6. Pitch pour le sprint d'innovation

**Accroche :** « Combien de temps faut-il aujourd'hui pour savoir d'où vient un champ d'un mart ? »

**Démonstration en 3 temps (≈ 5 min) :**

1. **Chercher** — je tape `code_tiers`, l'outil liste toutes les tables qui le portent, triées par couche.
2. **Remonter** — j'ouvre un mart, je clique sur une colonne : le chemin Mart → BV → Lac s'affiche, avec les transformations.
3. **Mesurer l'impact** — je pars d'une table du lac : tous les objets aval s'allument.
4. **Bonus collaboratif** — j'ajoute l'URL de la spec Confluence sur la bulle ; mon collègue la voit immédiatement.

**Messages clés :**

- Zéro installation, zéro IA, zéro donnée qui sort du poste.
- **Un simple JSON de structure suffit** : le lineage est reconstitué par des algorithmes explicables, puis fiabilisé par l'équipe.
- Le savoir de l'équipe devient **cumulatif** (annotations partagées).
- Base réutilisable pour d'autres équipes et d'autres missions.
