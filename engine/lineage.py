"""Flux de lineage certains (déclarés, SQL), arbitrage et parcours amont/aval.

- `declared_flows(md, catalog)`   : flux `flows[]` du JSON (origine DECLARED, score 1.0) ;
- `sql_flows(catalog)`            : flux déduits du champ `sql` (origine SQL_PARSED, 0.9) ;
- `parse_sql(sql, own_id, known)` : parsing regex (cible, sources, colonnes simples) ;
- `arbitrate(edges)`              : garde l'origine la plus fiable par couple (S, C) ;
- `traverse(start, direction, neighbours, ...)` : BFS borné avec détection de cycles.

Si `sqlglot` est installé, il n'est pas encore utilisé : le parser regex couvre
les SELECT simples (colonne, alias, fonction sur une colonne).
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

from .catalog import Catalog

ORIGIN_RANK = {"MANUAL": 4, "DECLARED": 3, "SQL_PARSED": 2, "INFERRED": 1}
ORIGIN_SCORE = {"MANUAL": 1.0, "DECLARED": 1.0, "SQL_PARSED": 0.9}

# --------------------------------------------------------------------------- flux déclarés


def declared_flows(md: dict, catalog: Catalog) -> list[dict]:
    """Transforme `flows[]` en arêtes DECLARED (SQL_PARSED pour les flux issus de requêtes SQL)."""
    edges = []
    for flow in md["flows"]:
        if flow["source"] not in catalog.objects or flow["target"] not in catalog.objects:
            continue
        origin = flow.get("origin", "DECLARED")
        score = ORIGIN_SCORE.get(origin, 1.0)
        explanation = ("Flux lu dans une requête SQL" if origin == "SQL_PARSED" else "Flux déclaré par l'extraction") \
            + (f" (`{flow['job']}`)." if flow["job"] else ".")
        edges.append({
            "kind": "FLOW", "source_object": flow["source"], "target_object": flow["target"],
            "origin": origin, "score": score, "job": flow["job"], "transformation": None,
            "explanation": explanation, "details": {},
            "columns": [{"source": m["source"], "target": m["target"],
                         "transformation": m["transformation"], "score": score}
                        for m in flow["column_mapping"] if m["source"] and m["target"]],
        })
    return edges


# --------------------------------------------------------------------------- parsing SQL

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING_RE = re.compile(r"'(?:[^'\\]|\\.)*'")
_TARGET_RE = re.compile(r"\bINSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?([`\"\w]+\.[`\"\w]+)", re.IGNORECASE)
_SOURCE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([`\"\w]+(?:\.[`\"\w]+)?)(?:\s+(?:AS\s+)?(\w+))?", re.IGNORECASE)
_CTE_RE = re.compile(r"(?:\bWITH|,)\s*(\w+)\s+AS\s*\(", re.IGNORECASE)
_SELECT_RE = re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?(.*?)\s+FROM\b", re.IGNORECASE | re.DOTALL)
_ALIAS_RE = re.compile(r"^(.*?)\s+(?:AS\s+)?([`\"]?\w+[`\"]?)$", re.IGNORECASE | re.DOTALL)
_REF_RE = re.compile(r"\b(?:(\w+)\.)?(\w+)\b(?!\s*\()")
_RESERVED = {"as", "on", "and", "or", "not", "null", "case", "when", "then", "else", "end",
             "join", "left", "right", "inner", "outer", "full", "cross", "where", "group", "by",
             "order", "having", "limit", "where", "select", "distinct", "from", "where", "union",
             "all", "is", "in", "like", "between", "true", "false", "lateral", "view", "where"}


def _strip(sql: str) -> str:
    """Supprime commentaires et chaînes littérales."""
    return _STRING_RE.sub("''", _COMMENT_RE.sub(" ", sql))


def _unquote(name: str) -> str:
    return name.replace("`", "").replace('"', "").lower()


def _split_top_level(text: str) -> list[str]:
    """Découpe une liste SELECT sur les virgules de premier niveau."""
    parts, depth, current = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def parse_sql(sql: str, own_id: str, known: Iterable[str]) -> dict:
    """Analyse un SQL : cible, sources connues et correspondances de colonnes simples."""
    known = set(known)
    text = _strip(sql)
    target_match = _TARGET_RE.search(text)
    target = _unquote(target_match.group(1)) if target_match else own_id
    ctes = {m.group(1).lower() for m in _CTE_RE.finditer(text)}
    aliases: dict[str, str] = {}
    sources: list[str] = []
    for match in _SOURCE_RE.finditer(text):
        name = _unquote(match.group(1))
        if name in ctes or name not in known or name == target:
            continue
        if name not in sources:
            sources.append(name)
        alias = (match.group(2) or "").lower()
        if alias and alias not in _RESERVED:
            aliases[alias] = name
        aliases[name.split(".")[-1]] = name
        aliases[name] = name
    columns = []
    select = _SELECT_RE.search(text)
    if select and sources:
        for item in _split_top_level(select.group(1)):
            columns.extend(_parse_select_item(item, aliases, sources))
    return {"target": target, "sources": sources, "columns": columns}


def _parse_select_item(item: str, aliases: dict[str, str], sources: list[str]) -> list[dict]:
    """Une expression du SELECT → correspondances (source_object, source_col, target_col)."""
    if item.strip() == "*" or item.strip().endswith(".*"):
        return []
    expression, target_col = item, None
    alias_match = _ALIAS_RE.match(item)
    if (alias_match and alias_match.group(2).lower() not in _RESERVED
            and not item.rstrip().endswith(")")
            and alias_match.group(1).rstrip()[-1:] not in "+-*/%|=<>(,"):
        expression, target_col = alias_match.group(1), _unquote(alias_match.group(2))
    refs = []
    for match in _REF_RE.finditer(expression):
        qualifier, col = (match.group(1) or "").lower(), match.group(2).lower()
        if col in _RESERVED or col.isdigit():
            continue
        if qualifier:
            if qualifier in aliases:
                refs.append((aliases[qualifier], col))
        elif len(sources) == 1:
            refs.append((sources[0], col))
    if not refs:
        return []
    if target_col is None:
        if len(refs) != 1:
            return []
        target_col = refs[0][1]
    simple = len(refs) == 1 and re.fullmatch(r"\s*(?:\w+\.)?\w+\s*", expression)
    if simple:
        transformation = None if refs[0][1] == target_col else "renommage"
    else:
        transformation = " ".join(expression.split())
    return [{"source_object": src, "source": col, "target": target_col,
             "transformation": transformation} for src, col in refs]


def sql_flows(catalog: Catalog) -> list[dict]:
    """Arêtes SQL_PARSED pour tous les objets ayant un champ `sql`."""
    edges = []
    known = catalog.objects.keys()
    for obj in catalog.objects.values():
        if not obj.sql:
            continue
        parsed = parse_sql(obj.sql, obj.id, known)
        target = catalog.objects.get(parsed["target"])
        if target is None:
            continue
        target_cols = {c.name for c in target.columns}
        for source_id in parsed["sources"]:
            source_cols = {c.name for c in catalog.objects[source_id].columns}
            cols = [{"source": c["source"], "target": c["target"],
                     "transformation": c["transformation"], "score": 0.9}
                    for c in parsed["columns"]
                    if c["source_object"] == source_id and c["source"] in source_cols
                    and c["target"] in target_cols]
            edges.append({
                "kind": "FLOW", "source_object": source_id, "target_object": target.id,
                "origin": "SQL_PARSED", "score": 0.9, "job": None, "transformation": None,
                "explanation": f"Flux déduit du SQL de `{obj.id}`.", "details": {}, "columns": cols,
            })
    return edges


# --------------------------------------------------------------------------- arbitrage


def arbitrate(edges: list[dict]) -> list[dict]:
    """Garde, pour chaque couple (S, C), l'origine la plus fiable.

    Les correspondances colonne sont reprises de l'origine la plus fiable qui en possède ;
    un flux inféré confirmé par une source fiable est signalé dans l'explication.
    """
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for edge in edges:
        by_pair.setdefault((edge["source_object"], edge["target_object"]), []).append(edge)
    result = []
    for group in by_pair.values():
        group.sort(key=lambda e: (-ORIGIN_RANK[e["origin"]], -e["score"]))
        best = dict(group[0])
        if not best["columns"]:
            for other in group[1:]:
                if other["columns"]:
                    best["columns"] = other["columns"]
                    break
        inferred = [e for e in group if e["origin"] == "INFERRED"]
        if best["origin"] != "INFERRED" and inferred:
            best["explanation"] += f" Confirme le flux inféré (score {inferred[0]['score']:.2f})."
            best["details"] = {**best.get("details", {}), "inferred_score": inferred[0]["score"],
                               "unexplained": inferred[0]["details"].get("unexplained", [])}
        result.append(best)
    return result


# --------------------------------------------------------------------------- parcours


def traverse(start: str, direction: str, neighbours: Callable[[str, str], list[dict]],
             max_depth: int, min_score: float = 0.0, max_nodes: int = 10_000) -> tuple[list[dict], int]:
    """BFS amont (`up`) ou aval (`down`) borné, avec score de chemin (produit des scores).

    `neighbours(node, direction)` renvoie les arêtes FLOW déjà fusionnées avec les annotations
    (les arêtes rejetées sont exclues en amont). Renvoie (résultats, nb_non_affichés).
    """
    seen = {start: 1.0}
    frontier = [start]
    result: list[dict] = []
    truncated = 0
    for depth in range(1, max_depth + 1):
        nxt = []
        for node in frontier:
            for edge in neighbours(node, direction):
                other = edge["source_object"] if direction == "up" else edge["target_object"]
                if edge["score"] < min_score:
                    continue
                path_score = round(seen[node] * edge["score"], 3)
                result.append({"depth": depth, "edge": edge, "node": other, "path_score": path_score})
                if other in seen:
                    continue
                if len(seen) >= max_nodes:
                    truncated += 1
                    continue
                seen[other] = path_score
                nxt.append(other)
        frontier = nxt
        if not frontier:
            break
    return result, truncated
