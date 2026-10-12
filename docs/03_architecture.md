# 03 — Architecture technique

## 1. Vue d'ensemble

```
 Extraction (metastore, scripts)          Espace partagé (Teams)                 Chaque poste
 ─────────────────────────────           ────────────────────────               ─────────────────────────────
 JSON de structure ou fichiers .sql ──▶   metadonnees/  (imports)        ──▶   LineageExplorer.cmd
                                          journal/      (1 fichier /            ├─ serveur local 127.0.0.1
                                                         modification)  ◀──▶    ├─ cache SQLite local
                                          versions/     (instantanés)           │  (%LOCALAPPDATA%)
                                                                                └─ navigateur : interface web
```

- **Métadonnées** (tables, colonnes) : importées une fois dans `metadonnees/`. Chaque poste construit son **cache SQLite local** (index de recherche, dictionnaire des colonnes, suggestions).
- **Travail de l'équipe** (tags, liens, specs, notes, réglages, versions) : **journal d'événements** dans `journal/`, rejoué par chaque poste. Aucune base n'est partagée en écriture : c'est ce qui rend l'outil fiable dans un dossier synchronisé.
- **Rafraîchissement** : l'interface interroge `/api/revision` toutes les 15 s. Le serveur relit le journal ; si d'autres ont modifié quelque chose, la vue se met à jour et un message l'indique.

## 2. Choix techniques

| Composant | Choix | Justification |
|---|---|---|
| Moteur, serveur | Python ≥ 3.9, **bibliothèque standard uniquement** | Pas de `pip` nécessaire, exe autonome |
| Distribution | **Dossier portable** : Python officiel signé + code + `LineageExplorer.cmd` (`tools/build_portable.py`) | Rien à installer ; pas de faux positif antivirus (un .exe PyInstaller non signé est bloqué par Defender) |
| Partage | **Journal d'événements** (fichiers JSON uniques) | Zéro conflit de synchronisation, historique complet |
| Index | SQLite local + FTS5 `trigram` (repli `LIKE`) | Recherche instantanée sur tout le catalogue |
| Interface | HTML / CSS / JS vanilla, graphe SVG maison | Aucune librairie, aucun CDN |
| Export | `.xlsx` écrit avec `zipfile` (stdlib) | Documentation exploitable dans Excel |

## 3. Modules

```
engine/
├── app.py           # point d'entrée (LineageExplorer.cmd) : serveur + navigateur + petite fenêtre, instance unique
├── __main__.py      # CLI : check | import | serve | report | evaluate
├── config.py        # configuration, emplacements (espace partagé, cache local)
├── ingest.py        # lecture JSON / JSONL / SQL, validation
├── sql_ingest.py    # requêtes SQL → structure + indications de flux / jointures
├── normalize.py     # normalisation des noms, familles de types
├── catalog.py       # objets enrichis, fréquences des colonnes
├── suggestions.py   # suggestions de liens (colonnes communes + indications)
├── build.py         # construction du cache local
├── store.py         # SQLite : objets, colonnes, suggestions, recherche, dictionnaire
├── workspace.py     # ⭐ journal partagé, rejeu de l'état, versions, comparaisons
├── lineage.py       # analyse SQL bas niveau, parcours amont / aval
├── xlsx.py          # export Excel minimal
└── server.py        # API HTTP + fichiers statiques
```

## 4. API (locale)

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/state`, `/api/revision?since=` | État général, détection des modifications des collègues |
| GET | `/api/tree`, `/api/tree/objects?tag=&db=` | Catalogue Tag → Base → Objets |
| GET | `/api/search?q=` | Recherche sur tout le catalogue (`tag:` `base:` `col:` `table:`) |
| GET | `/api/object/{id}`, `/api/graph/{id}?dir=&depth=&sugg=&pending=&join=` | Détail, graphe |
| GET | `/api/column?name=&similar=`, `/api/columns?q=&conflicts=` | Vue colonne, dictionnaire |
| GET | `/api/lineage/column`, `/api/path`, `/api/impact/{id}.csv` | Lineage colonne, chemin, impact |
| GET | `/api/review`, `/api/suggestions` | Liens à valider, suggestions |
| GET | `/api/versions`, `/api/version?file=`, `/api/activity`, `/api/report`, `/api/export.xlsx` | Historique, rapport, export |
| POST | `/api/import?format=json\|jsonl\|sql` | Import dans l'espace partagé |
| POST | `/api/tags/save\|delete\|assign`, `/api/rules/save\|delete` | Tags et règles |
| POST | `/api/links/create\|update\|validate\|delete` | Liens |
| POST | `/api/suggestions/dismiss\|restore`, `/api/object/update`, `/api/notes/add\|delete`, `/api/settings/save` | Divers |
| POST | `/api/versions/save\|restore` | Versions |

L'auteur d'une modification est l'en-tête `X-User` (nom saisi dans l'interface) ou, à défaut, l'utilisateur Windows.

## 5. Sécurité

- Serveur lié à `127.0.0.1`, port exclusif (deux instances ne peuvent pas partager un port), aucun appel sortant.
- Échappement HTML systématique ; les URL de spec doivent commencer par `http(s)://` et ne sont jamais chargées par l'outil.
- Le dépôt public ne contient que du code et des exemples fictifs ; les données réelles restent dans l'espace Teams.
