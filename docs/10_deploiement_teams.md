# 10 — Déployer Lineage Explorer dans un espace Teams

Objectif : toute l'équipe utilise le **même outil**, voit les **mêmes tags, liens, specs et notes**, et chaque modification est enregistrée automatiquement dans le dossier Teams. **Rien n'est installé ni hébergé sur un PC personnel.**

## 1. Ce qu'on dépose dans Teams

Le dossier [`deploiement/LineageExplorer`](../deploiement/LineageExplorer) du dépôt GitHub, **tel quel** :

```
LineageExplorer\                      ← à déposer dans les fichiers du canal Teams
├── LineageExplorer.cmd               ← double-clic pour lancer l'outil
├── LISEZMOI.txt
├── app\                              ← l'outil (code + Python portable officiel, ne pas modifier)
│   ├── python\                       ← Python 3 signé par la Python Software Foundation
│   └── vendor\sqlglot\               ← analyseur SQL (pur Python, licence MIT)
├── metadonnees\   (créé tout seul)   ← les JSON / SQL importés
├── journal\       (créé tout seul)   ← un fichier par version et par poste (une ligne par modification)
└── versions\      (créé tout seul)   ← les « Enregistrer »
```

Sur chaque poste, seul un **index de recherche** est recréé automatiquement dans `%LOCALAPPDATA%\LineageExplorer` (avec le journal technique `lineage_explorer.log`). Il ne contient aucune donnée de l'équipe qui n'existe pas déjà dans Teams. On peut le supprimer sans risque.

> **Pourquoi un `.cmd` et pas un `.exe` ?** Un `.exe` fabriqué avec PyInstaller, non signé, est classé comme menace par Microsoft Defender (`Trojan:Win32/Bearfoos.A!ml`, faux positif connu) : il est supprimé ou arrêté en pleine utilisation. Le Python officiel signé n'a pas ce problème. Le dossier a été analysé par Defender : aucune menace.

## 2. Mise en place (une seule fois)

1. **Télécharger depuis GitHub**, sur le PC du travail : https://github.com/Yaniss0712/lineage-explorer → **Code → Download ZIP**. Dézipper, puis prendre le dossier `deploiement\LineageExplorer`.
2. Dans le canal Teams de l'équipe : **Fichiers → Charger → Dossier** → choisir ce dossier `LineageExplorer`.
3. **Synchroniser**, ou **Ajouter un raccourci à Mon OneDrive**, pour que le dossier apparaisse dans l'Explorateur Windows.
4. Dans l'Explorateur : clic droit sur le dossier `LineageExplorer` → **Toujours conserver sur cet appareil**. C'est indispensable : l'outil lit ses fichiers en local.
5. Double-clic sur **`LineageExplorer.cmd`** :
   - le navigateur s'ouvre sur l'outil ;
   - une petite fenêtre « Lineage Explorer est ouvert » apparaît. **La fermer arrête l'outil.**
   - Relancer le `.cmd` alors que l'outil tourne déjà rouvre simplement l'onglet.
6. **Importer** → le JSON extrait du metastore (ou des fichiers `.sql`). Il est copié dans `metadonnees\` : tout le monde l'a.
7. **Tags** → créer les tags et les règles de nommage (par exemple `fiche_client_pj_*` → « Fiche client »).

## 3. Pour chaque membre de l'équipe

1. Teams : canal → **Fichiers** → `LineageExplorer` → **Synchroniser**, puis **Toujours conserver sur cet appareil**.
2. Double-clic sur `LineageExplorer.cmd`. Un raccourci sur le bureau est possible : clic droit → **Envoyer vers** → **Bureau**.
3. Le nom de l'auteur vient de la session Windows ; on peut le changer dans le menu **⋯**.

## 4. Au quotidien

| Action | Où | Enregistré dans |
|---|---|---|
| Taguer | Détail de la table → Tag, ou « Sélection multiple » dans le catalogue | `journal\` |
| Créer un lien | « Ajouter un lien », une suggestion, la vue colonne, ou un import SQL | `journal\` (flux « à valider », jointure validée) |
| Créer un objet (objet métier, viz, table prévue) | **+ Objet** dans le catalogue | `journal\` (étiquette New) |
| Fusionner une table New | **Importer → Fusionner une table New** (SQL ou JSON) | `metadonnees\` + `journal\` |
| Annuler / revenir en arrière | **Ctrl+Z / Ctrl+Y**, ou **Historique** → annuler une ligne, revenir à un point | `journal\` (rien n'est effacé) |
| Signaler une anomalie | Onglet **Suivi**, ou « Signaler » sur un objet / un lien | `journal\` |
| Valider / supprimer un lien | Onglet « À valider » ou onglet Liens | `journal\` |
| Documenter | Détail → Doc (description, propriétaire, URL de spec, notes) | `journal\` |
| Recharger les données | Bouton **Actualiser** (en haut) | — |
| Enregistrer la version | Bouton **Enregistrer** | `versions\` |
| Comparer / restaurer | **Historique** | `journal\` |
| Nouvelle extraction | **Importer** | `metadonnees\` (tags et liens conservés) |

- L'outil relit le journal toutes les 15 secondes. Une modification d'un collègue arrive en général en quelques secondes à une minute, selon la synchronisation Teams.
- **À ne pas faire** : modifier ou supprimer à la main les fichiers de `journal\`, `versions\`, `metadonnees\` ou `app\`.

## 5. Mettre à jour l'outil

1. Télécharger la nouvelle version depuis GitHub (**Download ZIP**).
2. Demander à l'équipe de fermer l'outil (fermer la petite fenêtre).
3. Dans le dossier Teams, remplacer **uniquement** `app\`, `LineageExplorer.cmd` et `FusionnerJournal.cmd`. `journal\`, `versions\` et `metadonnees\` ne bougent pas : rien n'est perdu.

### Passer de la v0.3 à la v0.4 : regrouper le journal

Jusqu'à la v0.3, chaque modification était un petit fichier `.json` dans `journal\`. La v0.4 les **regroupe en un seul fichier** `journal\archive__….jsonl` (une ligne par modification, même contenu, ordre chronologique), puis écrit un fichier par version et par poste.

1. **Tout le monde ferme l'outil** et passe à la v0.4 en même temps : une v0.3 ne sait pas lire le nouveau fichier, elle verrait un journal vide.
2. Vérifier que Teams a fini de synchroniser `journal\` (plus d'icône de synchronisation).
3. Une seule personne lance la v0.4 (`LineageExplorer.cmd`) : le regroupement est **automatique**. Ou double-clic sur **`FusionnerJournal.cmd`**, qui affiche le bilan (nombre de fichiers, de modifications) avant de regrouper.
4. Sécurités : la copie est relue avant toute suppression ; l'état de l'équipe (tags, liens, docs…) est comparé avant / après, et rien n'est supprimé s'il diffère ; les anciens fichiers sont gardés dans `journal\sauvegardes\journal_avant_regroupement_….zip`. Un fichier encore en cours de synchronisation est laissé en place et regroupé au lancement suivant.
5. Les autres lancent ensuite la v0.4 normalement.

En ligne de commande : `app\python\python.exe -m engine compact --workspace <dossier Teams> --dry-run` (bilan seul), puis sans `--dry-run`.

## 6. En cas de problème

| Symptôme | Piste |
|---|---|
| Bandeau « Connexion perdue » | La petite fenêtre a été fermée, ou le PC est sorti de veille : relancer `LineageExplorer.cmd`, puis **Réessayer** |
| Données pas à jour | Bouton **Actualiser** |
| Rien ne s'ouvre | Lire `%LOCALAPPDATA%\LineageExplorer\lineage_explorer.log` ; vérifier que le dossier est bien « Toujours conserver sur cet appareil » |
| Lancement interdit depuis OneDrive (stratégie de l'entreprise) | Copier `app\` et `LineageExplorer.cmd` dans un dossier local (ex. `C:\Outils\LineageExplorer`), puis dans le `.cmd` remplacer la ligne `set "LINEAGE_WORKSPACE=%~dp0"` par le chemin du dossier Teams synchronisé |
| Recherche incohérente | Fermer l'outil, supprimer `%LOCALAPPDATA%\LineageExplorer\cache\`, relancer |

## 7. Sécurité

- L'outil n'écoute que sur `127.0.0.1` (le poste lui-même) et ne fait **aucun appel réseau sortant**.
- Le dossier Teams contient les vrais noms de tables et de colonnes : il reste **interne**. Le dépôt GitHub public ne contient que le code et des exemples fictifs.
