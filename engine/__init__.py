"""Lineage Explorer — moteur de lineage de données (Python standard uniquement).

Modules :
- config      : lecture de la configuration et valeurs par défaut ;
- ingest      : lecture / validation du JSON (ou JSON Lines) d'entrée ;
- normalize   : normalisation des noms de colonnes et d'objets ;
- layers      : attribution des couches LAC / BV / MART ;
- inference   : inférence du lineage à partir de la structure seule ;
- lineage     : flux déclarés, parsing SQL, arbitrage, parcours amont/aval ;
- relations   : détection des relations de clés ;
- store       : base SQLite (schéma, écriture, requêtes) ;
- annotations : annotations partagées (spec, liens manuels, validation) ;
- build       : orchestration du build complet ;
- evaluate    : précision / rappel sur une vérité terrain ;
- server      : API HTTP locale + interface web.
"""

__version__ = "0.1.0"
