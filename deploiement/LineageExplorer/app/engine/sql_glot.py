"""Analyse des requêtes SQL avec sqlglot (vrai analyseur syntaxique, pur Python, licence MIT).

sqlglot est fourni dans `vendor/` (aucune installation). Il comprend ce que les expressions
régulières de `sql_ingest` ne savent pas lire : sous-requêtes, `WITH` (CTE), `UNION`,
`CASE`, fonctions de fenêtre, `INSERT OVERWRITE … PARTITION`, dialectes Hive / Spark.

`analyze(statement, default_db, known_columns)` renvoie, pour une requête (vue, CTAS,
INSERT … SELECT, SELECT), un dict :

    kind        VIEW | CTAS | INSERT | SELECT
    target      table alimentée (ou None) ; target_cols : colonnes déclarées de la cible
    sources     tables réellement lues (CTE et sous-requêtes résolues jusqu'aux tables)
    columns     [{name, expression, plain, refs: [(table, colonne)]}] : lineage de colonne
    used        {table: {colonnes lues}}
    relations   [(table_a, col_a, table_b, col_b)] : égalités des jointures (ON / USING / WHERE)
    rule        règle de gestion lisible (from, joins, filters, group_by, having, steps)

ou None si la requête n'est pas comprise (l'analyse par expressions régulières prend le relais).
Les DDL `CREATE TABLE (colonnes)` restent lus par `sql_ingest` (déjà fiable).
"""

from __future__ import annotations

import os
import re
import sys
from typing import Callable, Optional

_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

try:  # noqa: SIM105
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.scope import Scope, build_scope
    AVAILABLE = True
except Exception:  # noqa: BLE001 — sqlglot absent ou incompatible : analyse par expressions régulières
    AVAILABLE = False

DIALECTS = ("hive", "spark", None)
_MAX_DEPTH = 40


def parse(statement: str):
    """Arbre syntaxique de l'instruction (premier dialecte qui la comprend), sinon None."""
    if not AVAILABLE:
        return None
    for dialect in DIALECTS:
        try:
            tree = sqlglot.parse_one(statement, read=dialect)
        except Exception:  # noqa: BLE001
            continue
        if tree is not None and not isinstance(tree, exp.Command):
            return tree
    return None


def _arg(node, *names):
    for name in names:
        value = node.args.get(name)
        if value is not None:
            return value
    return None


def _is_set(scope) -> bool:
    return isinstance(scope.expression, exp.SetOperation)


def table_id(table, default_db: str) -> str:
    db = (table.db or default_db or "default").lower()
    return f"{db}.{table.name.lower()}"


class _Analyzer:
    def __init__(self, default_db: str, known_columns: Callable[[str], list]):
        self.default_db = default_db
        self.known_columns = known_columns
        self.unresolved: list[str] = []
        self.trail: list[str] = []

    # ------------------------------------------------------------------ résolution des colonnes

    def _sources(self, scope) -> dict:
        return {str(k).lower(): v for k, v in scope.sources.items()}

    def resolve_column(self, scope, col, depth: int = 0) -> list[tuple[str, str]]:
        """Tables et colonnes de base derrière une colonne d'une portée (CTE / sous-requêtes suivies)."""
        if depth > _MAX_DEPTH:
            return []
        name, qual = col.name.lower(), (col.table or "").lower()
        sources = self._sources(scope)
        if qual:
            src = sources.get(qual)
            if src is None:   # nom de table utilisé comme qualifiant alors qu'un alias existe
                src = next((s for s in sources.values() if isinstance(s, exp.Table) and s.name.lower() == qual), None)
            if src is None:
                return []
            return self._from_source(src, name, depth)
        if len(sources) == 1:
            return self._from_source(next(iter(sources.values())), name, depth)
        for src in sources.values():          # plusieurs sources : celle qui expose la colonne
            if isinstance(src, Scope) and name in self._output_names(src):
                return self._from_source(src, name, depth)
            if isinstance(src, exp.Table) and name in (self.known_columns(table_id(src, self.default_db)) or []):
                return self._from_source(src, name, depth)
        if sources and name not in ("*",):
            self.unresolved.append(name)
        return []

    def _from_source(self, src, name: str, depth: int) -> list[tuple[str, str]]:
        if isinstance(src, exp.Table):
            return [(table_id(src, self.default_db), name)]
        if isinstance(src, Scope):
            return self.resolve_output(src, name, depth + 1)
        return []

    def _projections(self, scope) -> list:
        node = scope.expression
        return list(node.expressions) if isinstance(node, exp.Select) else []

    def _output_names(self, scope) -> list[str]:
        if _is_set(scope):
            return self._output_names(scope.set_operation_scopes[0])
        return [p.alias_or_name.lower() for p in self._projections(scope)]

    def resolve_output(self, scope, name: str, depth: int = 0) -> list[tuple[str, str]]:
        """Origines d'une colonne produite par une portée (SELECT, UNION…)."""
        if depth > _MAX_DEPTH:
            return []
        if _is_set(scope):
            names = self._output_names(scope.set_operation_scopes[0])
            if name not in names:
                return []
            index, out = names.index(name), []
            for branch in self._union_branches(scope):
                projs = self._projections(branch)
                if index < len(projs):
                    for ref in self._refs(branch, projs[index], depth):
                        if ref not in out:
                            out.append(ref)
            return out
        for proj in self._projections(scope):
            if proj.alias_or_name.lower() == name:
                inner = proj.this if isinstance(proj, exp.Alias) else proj
                if not isinstance(inner, exp.Column):
                    self.trail.append(self.readable(inner, scope))      # calcul fait dans une CTE / sous-requête
                return self._refs(scope, proj, depth)
        if any(isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star))
               for p in self._projections(scope)):
            return self.resolve_column(scope, exp.column(name), depth + 1)
        return []

    def _union_branches(self, scope) -> list:
        out = []
        for branch in scope.set_operation_scopes:
            out.extend(self._union_branches(branch) if _is_set(branch) else [branch])
        return out

    def _refs(self, scope, proj, depth: int) -> list[tuple[str, str]]:
        out = []
        for col in proj.find_all(exp.Column):
            if isinstance(col.this, exp.Star):
                continue
            if col.find_ancestor(exp.Select) is not scope.expression:
                continue                                     # colonne d'une sous-requête scalaire
            for ref in self.resolve_column(scope, col, depth):
                if ref not in out:
                    out.append(ref)
        return out

    # ------------------------------------------------------------------ lecture d'une requête

    def readable(self, node, scope) -> Optional[str]:
        """Texte SQL avec les alias remplacés par les noms de tables (ou de CTE)."""
        if node is None:
            return None
        labels = {alias: self._short(src, alias) for alias, src in self._sources(scope).items()}
        text = node.sql(dialect="hive", normalize_functions="lower")
        text = re.sub(r"`(\w+)`", r"\1", text)
        return " ".join(re.sub(r"\b(\w+)\.(?=\w)", lambda m: f"{labels.get(m.group(1).lower(), m.group(1))}.", text).split())

    @staticmethod
    def _short(src, alias: str) -> str:
        """Nom lisible d'une source : nom de table, nom de la CTE, sinon l'alias."""
        if isinstance(src, exp.Table):
            return src.name.lower()
        parent = getattr(getattr(src, "expression", None), "parent", None)
        if isinstance(parent, exp.CTE) and parent.alias_or_name:
            return parent.alias_or_name.lower()
        return alias

    def _label(self, src, alias: str) -> Optional[str]:
        if isinstance(src, exp.Table):
            return table_id(src, self.default_db)
        return f"({self._short(src, alias)})"

    def clauses(self, scope) -> dict:
        """FROM, jointures (type, table, condition), filtres, regroupement d'une portée SELECT."""
        select = scope.expression
        if not isinstance(select, exp.Select):
            return {}
        sources = self._sources(scope)
        from_ = _arg(select, "from_", "from")
        first = None
        if from_ is not None and from_.this is not None:
            alias = (from_.this.alias_or_name or "").lower()
            first = self._label(sources.get(alias, from_.this), alias)
        joins = []
        for join in select.args.get("joins") or []:
            alias = (join.this.alias_or_name or "").lower()
            kind = " ".join(x for x in (join.side, join.kind) if x).upper()
            condition = join.args.get("on")
            using = join.args.get("using")
            text = self.readable(condition, scope) if condition is not None else (
                "USING (" + ", ".join(u.name for u in using) + ")" if using else "")
            joins.append({"type": f"{kind} JOIN".strip(), "table": self._label(sources.get(alias, join.this), alias),
                          "condition": text or ""})
        where, group, having = select.args.get("where"), select.args.get("group"), select.args.get("having")
        return {
            "from": first, "joins": joins,
            "filters": self.readable(where.this, scope) if where is not None else None,
            "group_by": ", ".join(self.readable(g, scope) for g in group.expressions) if group is not None else None,
            "having": self.readable(having.this, scope) if having is not None else None,
        }

    def relations(self, root) -> list[tuple[str, str, str, str]]:
        out = []

        def add(scope, left, right):
            a, b = self.resolve_column(scope, left), self.resolve_column(scope, right)
            if len(a) == 1 and len(b) == 1 and a[0][0] != b[0][0]:
                rel = (a[0][0], a[0][1], b[0][0], b[0][1])
                if rel not in out:
                    out.append(rel)

        for scope in root.traverse():
            select = scope.expression
            if not isinstance(select, exp.Select):
                continue
            conditions = [j.args.get("on") for j in select.args.get("joins") or [] if j.args.get("on") is not None]
            if select.args.get("where") is not None:
                conditions.append(select.args["where"].this)
            for cond in conditions:
                for eq in cond.find_all(exp.EQ):
                    if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Column):
                        add(scope, eq.this, eq.expression)
            sources = self._sources(scope)
            from_ = _arg(select, "from_", "from")
            for join in select.args.get("joins") or []:
                for using in join.args.get("using") or []:
                    left_alias = (from_.this.alias_or_name or "").lower() if from_ is not None else ""
                    right_alias = (join.this.alias_or_name or "").lower()
                    if left_alias in sources and right_alias in sources:
                        add(scope, exp.column(using.name, table=left_alias), exp.column(using.name, table=right_alias))
        return out

    def analyze(self, tree) -> Optional[dict]:
        kind, target, target_cols, query = None, None, None, None
        if isinstance(tree, exp.Insert):
            kind, query = "INSERT", tree.expression
            dest = tree.this
            if isinstance(dest, exp.Schema):
                target_cols = [c.name.lower() for c in dest.expressions]
                dest = dest.this
            target = table_id(dest, self.default_db) if isinstance(dest, exp.Table) else None
        elif isinstance(tree, exp.Create):
            query = tree.expression
            if not isinstance(query, exp.Query):
                return None                                   # DDL : lu par sql_ingest
            kind = "VIEW" if str(tree.args.get("kind") or "").upper() == "VIEW" else "CTAS"
            dest = tree.this
            if isinstance(dest, exp.Schema):
                cols = [c.name.lower() for c in dest.expressions if isinstance(c, (exp.Identifier, exp.ColumnDef, exp.Column))]
                target_cols = cols or None
                dest = dest.this
            target = table_id(dest, self.default_db) if isinstance(dest, exp.Table) else None
        elif isinstance(tree, exp.Query):
            kind, query = "SELECT", tree
        if query is None or not isinstance(query, exp.Query) or (kind != "SELECT" and target is None):
            return None
        with_ = _arg(tree, "with_", "with")
        if with_ is not None and _arg(query, "with_", "with") is None and query is not tree:
            query = query.copy()
            query.set("with_" if "with_" in type(query).arg_types else "with", with_.copy())
        try:
            root = build_scope(query)
        except Exception:  # noqa: BLE001
            return None
        if root is None:
            return None

        sources, used = [], {}
        for scope in root.traverse():
            for alias, src in self._sources(scope).items():
                if isinstance(src, exp.Table):
                    tid = table_id(src, self.default_db)
                    if tid != target and tid not in sources:
                        sources.append(tid)
            for col in scope.columns:
                if isinstance(col.this, exp.Star):
                    continue
                qual = (col.table or "").lower()
                srcs = self._sources(scope)
                src = srcs.get(qual) if qual else (next(iter(srcs.values())) if len(srcs) == 1 else None)
                if isinstance(src, exp.Table):
                    used.setdefault(table_id(src, self.default_db), set()).add(col.name.lower())

        main = root.set_operation_scopes[0] if _is_set(root) else root
        while _is_set(main):
            main = main.set_operation_scopes[0]
        columns, position = [], 0
        for proj in self._projections(main):
            inner = proj.this if isinstance(proj, exp.Alias) else proj
            star = isinstance(proj, exp.Star) or (isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star))
            if star:
                qual = (proj.table or "").lower() if isinstance(proj, exp.Column) else ""
                srcs = self._sources(main)
                picked = [srcs[qual]] if qual and qual in srcs else list(srcs.values())
                for src in picked:
                    names = (self.known_columns(table_id(src, self.default_db)) or []) if isinstance(src, exp.Table) \
                        else self._output_names(src) if isinstance(src, Scope) else []
                    for name in names:
                        columns.append({"name": name, "expression": name, "plain": True,
                                        "refs": self._from_source(src, name, 0), "via": []})
                        position += 1
                continue
            position += 1
            name = proj.alias_or_name.lower() if proj.alias_or_name else f"expr_{position}"
            self.trail = []
            if _is_set(root):
                refs = self.resolve_output(root, name) if name in self._output_names(root) else []
            else:
                refs = self._refs(main, proj, 0)
            columns.append({"name": name, "expression": self.readable(inner, main), "plain": isinstance(inner, exp.Column),
                            "refs": refs, "via": [t for t in self.trail if t]})

        rule = self.clauses(main)
        steps = []
        for scope in root.traverse():
            if scope is main or not isinstance(scope.expression, exp.Select):
                continue
            info = self.clauses(scope)
            parent = scope.expression.parent
            label = parent.alias_or_name if isinstance(parent, (exp.CTE, exp.Subquery)) and parent.alias_or_name else \
                ("UNION" if _is_set(scope) or isinstance(parent, exp.Union) else "sous-requête")
            if info.get("joins") or info.get("filters") or info.get("group_by") or info.get("having"):
                steps.append({"name": label, **info})
        rule["steps"] = steps
        return {"kind": kind, "target": target, "target_cols": target_cols, "sources": sources, "used": used,
                "columns": columns, "relations": self.relations(root), "rule": rule,
                "unresolved": sorted(set(self.unresolved))}


def analyze(statement: str, default_db: str = "default", known_columns: Callable[[str], list] | None = None) -> Optional[dict]:
    """Analyse une requête ; None si sqlglot est absent ou si la requête n'est pas comprise."""
    tree = parse(statement)
    if tree is None:
        return None
    try:
        return _Analyzer(default_db, known_columns or (lambda _t: [])).analyze(tree)
    except Exception:  # noqa: BLE001 — requête inattendue : repli sur les expressions régulières
        return None
