"""Génère un jeu de données FICTIF (structure seule) + sa vérité terrain, pour tester l'outil.

Usage :
    python tools/generate_sample.py                       # ~150 objets
    python tools/generate_sample.py --domains 200 --jsonl # gros volume (test de performance)

Produit (par défaut dans examples/) :
- metadata.generated.json (ou .jsonl) : objets Lac / BV / Mart, colonnes, types, clés ;
- ground_truth.generated.json         : les vrais flux, pour `python -m engine evaluate`.

Le générateur imite des cas réels : copies Lac → BV, colonnes renommées ou calculées,
jointures (plusieurs sources), BV enrichies construites sur d'autres BV, marts agrégés,
colonnes techniques et génériques présentes partout.
"""

from __future__ import annotations

import argparse
import json
import os
import random

DOMAINS = ["pret", "compte", "carte", "epargne", "assurance", "paiement", "credit", "titre",
           "garantie", "sinistre", "contentieux", "patrimoine", "leasing", "change", "virement"]
NOUNS = ["contrat", "echeance", "operation", "mouvement", "dossier", "souscription", "evenement",
         "produit", "agence", "offre", "frais", "incident", "demande", "reglement", "avenant"]
MEASURES = ["montant", "taux", "nb", "duree", "solde", "date_debut", "date_fin", "date_effet",
            "mode", "canal", "motif", "type", "categorie", "devise", "periodicite", "indice"]
TYPES = {"montant": "decimal(18,2)", "taux": "decimal(9,6)", "nb": "int", "duree": "int", "solde": "decimal(18,2)",
         "date_debut": "date", "date_fin": "date", "date_effet": "date", "indice": "decimal(9,4)"}
RENAMES = ["valeur", "mt", "qte", "lib", "ind", "cd", "val", "flag"]
GENERIC = [("libelle", "string"), ("statut", "string"), ("code", "string")]
TECH = [("date_chargement", "timestamp"), ("id_traitement", "bigint")]


def col(name: str, ctype: str = "string", key: bool = False) -> dict:
    data = {"name": name, "type": ctype}
    if key:
        data["is_key"] = True
    return data


def attr_type(name: str) -> str:
    for prefix, ctype in TYPES.items():
        if name.startswith(prefix):
            return ctype
    return "string"


def make_object(database: str, name: str, columns: list[dict], otype: str = "TABLE", desc: str = "") -> dict:
    for i, c in enumerate(columns, 1):
        c["position"] = i
    return {"id": f"{database}.{name}", "database": database, "name": name, "type": otype,
            "description": desc, "columns": columns}


def generate(nb_domains: int, seed: int) -> tuple[list[dict], list[dict]]:
    """Renvoie (objets, vérité terrain)."""
    rng = random.Random(seed)
    objects: list[dict] = []
    truth: list[dict] = []

    # Domaine transverse « client » : référentiel tiers utilisé par tous les marts.
    lac_cli = make_object("lac_crm", "clients_raw", [
        col("code_tiers", key=True), col("raison_sociale"), col("segment"), col("date_creation", "date"),
        col("code_postal"), col("pays"), *[col(n, t) for n, t in TECH]], desc="Clients bruts (fictif)")
    bv_cli = make_object("bv_client", "tiers", [
        col("code_tiers_ref", key=True), col("raison_sociale"), col("categorie_client"),
        col("date_creation", "date"), col("code_postal"), col("pays")], "VIEW", "Référentiel tiers (fictif)")
    objects += [lac_cli, bv_cli]
    truth.append({"source": lac_cli["id"], "target": bv_cli["id"]})

    for d in range(nb_domains):
        domain = DOMAINS[d % len(DOMAINS)] + (str(d // len(DOMAINS) + 1) if d >= len(DOMAINS) else "")
        nouns = rng.sample(NOUNS, rng.randint(3, 6))
        bv_objects = []
        for noun in nouns:
            attrs = rng.sample(MEASURES, rng.randint(4, 9))
            attr_cols = [f"{m}_{noun}" if rng.random() < 0.6 else m for m in attrs]
            lac_cols = [col(f"num_{noun}", key=True), col("code_tiers")]
            lac_cols += [col(a, attr_type(a)) for a in attr_cols]
            lac_cols += [col(n, t) for n, t in rng.sample(GENERIC, rng.randint(0, 2))]
            lac_cols += [col(n, t) for n, t in TECH]
            lac = make_object(f"lac_{domain}", f"{noun}_raw", lac_cols, desc=f"{noun} bruts ({domain}, fictif)")
            objects.append(lac)

            # BV : copie nettoyée, quelques colonnes renommées ou ajoutées par calcul.
            bv_cols = [col(f"num_{noun}", key=True), col("code_tiers_ref")]
            for a in attr_cols:
                r = rng.random()
                if r < 0.75:
                    bv_cols.append(col(a, attr_type(a)))
                elif r < 0.9:
                    bv_cols.append(col(f"{rng.choice(RENAMES)}_{noun}_{len(bv_cols)}", attr_type(a)))
            sources = [lac]
            if rng.random() < 0.3 and len(objects) > 3:  # jointure avec une autre table du lac du domaine
                others = [o for o in objects if o["database"] == f"lac_{domain}" and o is not lac]
                if others:
                    extra = rng.choice(others)
                    extra_attrs = [c for c in extra["columns"] if c["name"] not in {x["name"] for x in bv_cols}
                                   and not c.get("is_key") and c["name"] not in ("code_tiers", "date_chargement", "id_traitement")]
                    picked = rng.sample(extra_attrs, min(len(extra_attrs), rng.randint(2, 3)))
                    if len(picked) >= 2:
                        bv_cols += [col(c["name"], c["type"]) for c in picked]
                        bv_cols.append(col(extra["columns"][0]["name"], key=False))
                        sources.append(extra)
            bv_cols.append(col(f"ind_qualite_{noun}", "int"))
            bv = make_object(f"bv_{domain}", noun, bv_cols, "VIEW", f"{noun} ({domain}, fictif)")
            objects.append(bv)
            bv_objects.append(bv)
            truth += [{"source": s["id"], "target": bv["id"]} for s in sources]

        # BV enrichie construite sur une BV de base.
        if rng.random() < 0.5:
            base = rng.choice(bv_objects)
            enriched_cols = [col(c["name"], c["type"], c.get("is_key", False)) for c in base["columns"]]
            enriched_cols += [col("categorie_client"), col(f"score_{domain}", "decimal(9,4)")]
            enriched = make_object(base["database"], base["name"] + "_enrichi", enriched_cols, "VIEW")
            objects.append(enriched)
            truth += [{"source": base["id"], "target": enriched["id"]},
                      {"source": bv_cli["id"], "target": enriched["id"]}]

        # Marts : agrégats sur 1 à 2 BV du domaine + référentiel tiers.
        for m in range(rng.randint(1, 3)):
            picked = rng.sample(bv_objects, min(len(bv_objects), rng.randint(1, 2)))
            mart_cols = [col("code_tiers_ref", key=True), col("categorie_client"), col("raison_sociale")]
            for bv in picked:
                noun = bv["name"]
                attrs = [c for c in bv["columns"] if not c.get("is_key") and c["name"] != "code_tiers_ref"]
                for c in rng.sample(attrs, min(len(attrs), rng.randint(2, 4))):
                    if c["name"] not in {x["name"] for x in mart_cols}:
                        mart_cols.append(col(c["name"], c["type"]))
                mart_cols.append(col(f"total_{noun}", "decimal(18,2)"))
                mart_cols.append(col(f"nb_{noun}_actifs", "int"))
            mart = make_object(f"dm_{domain}", f"synthese_{m + 1}", mart_cols, desc=f"Mart {domain} n°{m + 1} (fictif)")
            objects.append(mart)
            truth += [{"source": bv["id"], "target": mart["id"]} for bv in picked]
            truth.append({"source": bv_cli["id"], "target": mart["id"]})
    return objects, truth


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--domains", type=int, default=12, help="nombre de domaines métier (défaut 12)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--jsonl", action="store_true", help="écrire au format JSON Lines")
    parser.add_argument("--out", default=os.path.join(root, "examples"), help="dossier de sortie")
    args = parser.parse_args()

    objects, truth = generate(args.domains, args.seed)
    meta = {"format_version": "1.0", "extracted_at": "2026-10-09T10:35:00+02:00",
            "source": "generateur-fictif", "extractor": f"generate_sample.py --domains {args.domains} --seed {args.seed}"}
    os.makedirs(args.out, exist_ok=True)
    if args.jsonl:
        path = os.path.join(args.out, "metadata.generated.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"record": "meta", **meta}, ensure_ascii=False) + "\n")
            for obj in objects:
                handle.write(json.dumps({"record": "object", **obj}, ensure_ascii=False) + "\n")
    else:
        path = os.path.join(args.out, "metadata.generated.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"meta": meta, "objects": objects}, handle, ensure_ascii=False, indent=1)
    truth_path = os.path.join(args.out, "ground_truth.generated.json")
    with open(truth_path, "w", encoding="utf-8") as handle:
        json.dump({"flows": truth}, handle, ensure_ascii=False, indent=1)
    nb_cols = sum(len(o["columns"]) for o in objects)
    print(f"{len(objects)} objets, {nb_cols} colonnes, {len(truth)} flux réels")
    print(f"→ {path}\n→ {truth_path}")


if __name__ == "__main__":
    main()
