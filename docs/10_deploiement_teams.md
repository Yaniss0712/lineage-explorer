# 10 — Déployer Lineage Explorer dans un espace Teams

Objectif : toute l'équipe utilise le **même outil**, voit les **mêmes tags, liens, specs et notes**, et chaque modification est enregistrée automatiquement dans le dossier Teams.

## 1. Comment ça marche

```
Dossier Teams « LineageExplorer » (synchronisé sur chaque poste)
├── LineageExplorer.exe     ← l'outil (un seul fichier, aucune installation)
├── config.json             ← facultatif : réglages communs
├── metadonnees\            ← les JSON / SQL importés (créé automatiquement)
├── journal\                ← 1 petit fichier par modification (créé automatiquement)
└── versions\               ← les « Enregistrer la version » (créé automatiquement)

Sur chaque poste (jamais dans Teams) :
%LOCALAPPDATA%\LineageExplorer\cache\   ← index de recherche, reconstruit tout seul
%LOCALAPPDATA%\LineageExplorer\lineage_explorer.log   ← journal technique en cas de souci
```

- Chaque action (créer un tag, un lien, valider, supprimer, documenter…) écrit **un nouveau fichier** dans `journal\`. Un fichier n'est jamais modifié ni écrasé : deux personnes qui travaillent en même temps ne créent **aucun conflit** de synchronisation Teams.
- Chaque poste relit `journal\` toutes les 15 secondes : les modifications des collègues apparaissent automatiquement. Un message indique qui a modifié quoi.
- Le bouton **« Enregistrer la version »** crée un instantané dans `versions\`. On peut le comparer à l'état actuel ou le **restaurer** depuis **Historique**.
- L'index local n'est **pas** dans Teams : une base de données synchronisée par OneDrive se corromprait.

## 2. Mise en place (une seule fois, par la personne qui administre l'outil)

1. **Récupérer l'exe** : sur la page GitHub du projet, onglet **Actions** → « Construire l'exécutable » → **Run workflow**. Au bout d'environ 3 minutes, télécharge l'artefact `LineageExplorer` (un zip qui contient l'exe). Une fois un tag `v*` publié, l'exe est aussi dans **Releases**. Autre possibilité : le construire toi-même (voir § 6).
2. Dans le canal Teams de l'équipe : **Fichiers** → **Nouveau dossier** → `LineageExplorer`.
3. Dépose `LineageExplorer.exe` dans ce dossier.
4. Clique sur **Synchroniser** (ou **Ajouter un raccourci à Mon OneDrive**) pour que le dossier apparaisse dans l'Explorateur Windows.
5. Dans l'Explorateur, clic droit sur le dossier `LineageExplorer` → **Toujours conserver sur cet appareil**. Ainsi, les fichiers du journal sont présents localement et la synchronisation est plus rapide.
6. Double-clique sur `LineageExplorer.exe`.
   - Au premier lancement, Windows peut afficher « Windows a protégé votre ordinateur ». Clique sur **Informations complémentaires**, puis **Exécuter quand même**. L'exe n'est pas signé numériquement ; si l'antivirus le bloque, demande au support informatique d'autoriser ce fichier.
   - Le navigateur s'ouvre sur l'outil, avec une petite fenêtre « Lineage Explorer est ouvert ». **Fermer cette fenêtre arrête l'outil.**
7. Clique sur **Importer** et dépose le JSON extrait du metastore (ou des fichiers `.sql`). Le fichier est copié dans `metadonnees\` : tout le monde l'a.
8. Ouvre **Tags**, puis crée les tags et les **règles de nommage** (par exemple `fiche_client_pj_*` → « Fiche client »).

## 3. Pour chaque membre de l'équipe

1. Dans Teams : canal → **Fichiers** → dossier `LineageExplorer` → **Synchroniser**.
2. Dans l'Explorateur : clic droit sur le dossier → **Toujours conserver sur cet appareil**.
3. Double-clic sur `LineageExplorer.exe`. Un raccourci sur le bureau est possible : clic droit sur l'exe → **Envoyer vers** → **Bureau**.
4. Le nom de l'auteur est pris de la session Windows. On peut le changer en haut à droite.

## 4. Si l'exécution d'un .exe depuis OneDrive est bloquée

Certaines entreprises interdisent de lancer un programme depuis un dossier synchronisé. Dans ce cas :

1. Copie `LineageExplorer.exe` dans un dossier local, par exemple `C:\Outils\LineageExplorer\`.
2. À côté de l'exe, crée un fichier `config.json` qui indique où est le dossier Teams :

```json
{ "paths": { "workspace": "%USERPROFILE%\\<Organisation>\\<Équipe> - Documents\\<Canal>\\LineageExplorer" } }
```

Le chemin exact est celui affiché par l'Explorateur quand tu ouvres le dossier synchronisé. `%USERPROFILE%` est remplacé automatiquement par le dossier de chaque utilisateur.

## 5. Au quotidien

| Action | Où | Ce qui est enregistré |
|---|---|---|
| Taguer un objet | Détail de la table → Tag, ou sélection multiple dans le catalogue | `journal\` |
| Créer un lien | « Ajouter un lien », une suggestion, ou la vue colonne | `journal\` (statut « à valider ») |
| Valider / supprimer un lien | Onglet « À valider », ou Liens dans le détail | `journal\` (avec auteur et date) |
| Documenter (description, propriétaire, URL de spec, notes) | Détail → Doc | `journal\` |
| Enregistrer la version | Bouton en haut | `versions\` |
| Comparer / restaurer | Historique → Versions | `journal\` (la restauration est tracée) |
| Nouvelle extraction | Importer | `metadonnees\` ; tags et liens conservés, différences affichées |

**À ne pas faire** : modifier ou supprimer à la main les fichiers de `journal\`, `versions\` ou `metadonnees\`. Pour annuler une erreur, utilise l'outil (supprimer le lien, restaurer une version).

**Délais** : une modification met généralement de quelques secondes à une minute pour arriver chez les collègues (temps de synchronisation Teams). L'indicateur en haut à droite montre l'heure de la dernière vérification.

## 6. Mettre à jour l'outil

1. Récupère la nouvelle version de `LineageExplorer.exe` (Releases GitHub, ou `python tools/build_exe.py` sur un poste de développement).
2. Demande à l'équipe de fermer l'outil : un exe en cours d'utilisation ne peut pas être remplacé.
3. Remplace l'exe dans le dossier Teams. `journal\`, `versions\` et `metadonnees\` ne bougent pas : rien n'est perdu.

## 7. Sauvegarde et sécurité

- Le dossier Teams (SharePoint) garde l'historique des fichiers. Le journal donne en plus l'historique complet des modifications, et les versions servent de points de restauration.
- L'outil n'écoute que sur `127.0.0.1` (le poste lui-même) et ne fait **aucun appel réseau sortant**.
- Le dossier Teams contient les noms réels des tables et colonnes : il doit rester **interne**. Ne publie jamais son contenu sur GitHub. Le dépôt public ne contient que le code et des exemples fictifs.

## 8. En cas de problème

| Symptôme | Piste |
|---|---|
| L'outil ne démarre pas | Lire `%LOCALAPPDATA%\LineageExplorer\lineage_explorer.log` |
| « hors ligne » en haut à droite | La petite fenêtre « Lineage Explorer est ouvert » a été fermée : relancer l'exe |
| Les modifications d'un collègue n'arrivent pas | Vérifier la synchronisation Teams / OneDrive (icône dans la barre des tâches) et « Toujours conserver sur cet appareil » |
| Recherche ou suggestions incohérentes | Fermer l'outil, supprimer `%LOCALAPPDATA%\LineageExplorer\cache\`, relancer (l'index est reconstruit) |
| Antivirus / SmartScreen | Demander l'autorisation de `LineageExplorer.exe` au support informatique |
