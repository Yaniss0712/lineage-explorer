# 09 — Brief pour les data engineers

> **À transmettre tel quel.** Ce document explique ce dont l'outil de lineage a besoin et pourquoi. Le format détaillé est dans `04_format_json.md`.

## Ce qu'on vous demande

Un fichier **JSON décrivant la structure** des données (Lac, BV, Mart), produit par un script Python. **Pas de données**, uniquement des métadonnées : noms de bases, de tables, de colonnes et types.

À partir de ce fichier, l'outil reconstitue par algorithmes le lineage (quelle table alimente quelle table), les liens de clés, et permet de naviguer dans l'architecture.

## Niveaux de contribution

Le niveau 1 suffit pour démarrer. Chaque niveau supplémentaire rend le lineage plus fiable.

| Niveau | Ce qu'il faut fournir | Effort estimé | Gain |
|---|---|---|---|
| **1 — Structure** ✅ | Bases, tables/vues, colonnes, types | Faible (lecture du metastore) | L'outil fonctionne |
| 3 — Flux ETL | Pour chaque job de l'outil ETL Python : source(s) et cible | Moyen (export des configs de jobs) | Lineage **certain** sur ces jobs |
| 4 — SQL des vues | Définition SQL des vues, quand elle est récupérable | Variable | Lineage certain + niveau colonne |
| ⭐ Vérité terrain | 20 à 50 flux que vous **savez** vrais | 1 h | Permet de mesurer la précision de l'outil |

## Format minimal attendu (niveau 1)

```json
{
  "meta": { "format_version": "1.0", "extracted_at": "2026-10-09T10:35:00+02:00", "source": "hive-prod" },
  "objects": [
    {
      "id": "lac_pret.contrats_raw",
      "database": "lac_pret",
      "name": "contrats_raw",
      "type": "TABLE",
      "columns": [
        { "name": "num_contrat", "type": "string" },
        { "name": "montant", "type": "decimal(18,2)" }
      ]
    }
  ]
}
```

Règles : identifiants en **minuscules**, `id` = `base.table`, encodage **UTF-8**. Pour un très gros volume, le format **JSON Lines** (un objet par ligne) est accepté (doc 04).

## Squelette de script d'extraction (indicatif)

À adapter à votre accès Hive (PyHive, impyla, client JDBC…) :

```python
import json
from datetime import datetime, timezone

def extract(cursor, databases):
    objects = []
    for db in databases:
        cursor.execute(f"SHOW TABLES IN {db}")
        for (table,) in cursor.fetchall():
            cursor.execute(f"DESCRIBE {db}.{table}")
            columns = []
            for pos, (name, col_type, comment) in enumerate(cursor.fetchall(), 1):
                if not name or name.startswith("#"):      # fin des colonnes (partitions…)
                    break
                columns.append({"name": name.strip().lower(), "type": col_type.strip(),
                                "description": (comment or "").strip(), "position": pos})
            objects.append({"id": f"{db}.{table}".lower(), "database": db.lower(),
                            "name": table.lower(), "type": "TABLE", "columns": columns})
    return {"meta": {"format_version": "1.0",
                     "extracted_at": datetime.now(timezone.utc).isoformat(),
                     "source": "hive"},
            "objects": objects}

# json.dump(extract(cur, ["lac_xxx", "bv_xxx", "dm_xxx"]), open("data/metadata.json", "w"),
#           ensure_ascii=False, indent=1)
```

Pour les **flux ETL** (niveau 3), le format attendu est simplement :

```json
"flows": [
  { "source": "lac_pret.contrats_raw", "target": "bv_pret.contrats", "job": "nom_du_job" }
]
```

## Questions pour vous

1. Quelles sont les **conventions de nommage** des tables (familles, préfixes) ? Elles servent à créer les règles de tags.
2. L'outil ETL Python stocke-t-il quelque part la **liste source → cible** de chaque job ?
3. Est-il possible de récupérer la définition SQL d'une partie des vues depuis le **metastore** ?
4. Les **clés** sont-elles documentées (convention de nommage, Data Vault `hk_`, fichier de référence) ?
5. Qui peut fournir une petite **liste de flux connus** pour mesurer la précision ?

## Ce que vous y gagnez

- Moins de questions du type « d'où vient ce champ ? ».
- Une analyse d'impact en un clic avant de modifier une table du lac.
- Une documentation vivante, alimentée par toute l'équipe.
