"""Lineage Explorer — outil de lineage collaboratif (Python standard uniquement).

Modules :
- config      : configuration et emplacements (espace partagé, cache local) ;
- ingest      : lecture / validation des métadonnées (JSON, JSON Lines, SQL) ;
- sql_ingest  : requêtes SQL → structure + indications de flux et de jointures ;
- normalize   : normalisation des noms de colonnes et d'objets ;
- catalog     : objets enrichis et fréquences des colonnes ;
- suggestions : suggestions de liens (aucun lien n'est créé automatiquement) ;
- build       : construction du cache local ;
- store       : cache SQLite (recherche, dictionnaire des colonnes, suggestions) ;
- workspace   : journal partagé, état de l'équipe (tags, liens, documentation), versions ;
- lineage     : analyse SQL bas niveau, parcours amont / aval ;
- xlsx        : export Excel ;
- server      : API HTTP locale + interface web ;
- app         : point d'entrée de l'exécutable Windows.
"""

__version__ = "0.2.0"
