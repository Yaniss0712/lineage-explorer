"""Analyse SQL de bas niveau (utilisée par `sql_ingest`) et parcours amont / aval du graphe des liens.

- `parse_sql(sql, own_id, known)`      : cible, sources et correspondances de colonnes simples ;
- `_parse_select_item(...)`            : une expression du SELECT → colonnes sources / cible ;
- `traverse(start, direction, neighbours, ...)` : BFS borné, détection de cycles, score de chemin.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

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


# --------------------------------------------------------------------------- parcours


def traverse(start: str, direction: str, neighbours: Callable[[str, str], list[dict]],
             max_depth: int, min_score: float = 0.0, max_nodes: int = 10_000) -> tuple[list[dict], int]:
    """BFS amont (`up`) ou aval (`down`) borné, avec score de chemin (produit des scores).

    `neighbours(node, direction)` renvoie les liens de type flux (validés et / ou à valider)
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
