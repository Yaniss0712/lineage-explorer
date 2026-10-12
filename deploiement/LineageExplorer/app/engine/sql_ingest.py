"""Ingestion de requêtes SQL : déduit la structure (tables, colonnes, clés), le lineage et les jointures.

Instructions reconnues (dialectes Hive / Spark / ANSI courants, parsing par expressions régulières) :
- `CREATE [EXTERNAL] TABLE t (col type [PRIMARY KEY] [REFERENCES x(c)] [COMMENT '...'], ...,
   PRIMARY KEY (...), FOREIGN KEY (...) REFERENCES x(...)) [PARTITIONED BY (...)]`
   → objet TABLE avec colonnes, types, clés, description, relations ;
- `CREATE [OR REPLACE] VIEW v [(cols)] AS SELECT ...` → objet VIEW + flux colonne à colonne ;
- `CREATE TABLE t AS SELECT ...` (CTAS) → objet TABLE + flux ;
- `INSERT INTO|OVERWRITE [TABLE] t [(cols)] SELECT ...` → flux (et objet si non déclaré) ;
- `ALTER TABLE t ADD [CONSTRAINT x] FOREIGN KEY (c) REFERENCES u (d)` → relation ;
- `SELECT ...` simple (requête d'analyse) → tables sources et jointures.

Une table seulement lue (FROM / JOIN) mais jamais déclarée est créée avec les colonnes que les
requêtes y référencent (`alias.colonne`) : la structure est reconstituée à partir de l'usage.
Les conditions `a.x = b.y` (ON / WHERE) deviennent des relations de clés.

Point d'entrée : `parse_sql_script(text, default_database)` → dict au format du contrat JSON
(docs/04_format_json.md), à passer ensuite à `ingest.parse_metadata`.
"""

from __future__ import annotations

import re
from collections import OrderedDict

from .lineage import (_COMMENT_RE, _CTE_RE, _RESERVED, _SOURCE_RE, _parse_select_item, _split_top_level,
                      _strip, _unquote)

SQL_ORIGIN = "SQL_PARSED"
_IDENT = r"[`\"\[]?[\w$]+[`\"\]]?(?:\s*\.\s*[`\"\[]?[\w$]+[`\"\]]?)?"
_CREATE_TABLE_RE = re.compile(
    rf"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:GLOBAL\s+|LOCAL\s+)?(?:TEMPORARY\s+|TEMP\s+)?(?:EXTERNAL\s+)?TABLE\s+"
    rf"(?:IF\s+NOT\s+EXISTS\s+)?({_IDENT})\s*(.*)$", re.IGNORECASE | re.DOTALL)
_CREATE_VIEW_RE = re.compile(
    rf"^CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMPORARY\s+|TEMP\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    rf"({_IDENT})\s*(\([^()]*\))?\s*(?:COMMENT\s+'(?:[^']|'')*'\s*)?(?:TBLPROPERTIES\s*\(.*?\)\s*)?AS\s+(.*)$",
    re.IGNORECASE | re.DOTALL)
_INSERT_RE = re.compile(
    rf"^INSERT\s+(?:INTO|OVERWRITE)\s+(?:TABLE\s+)?({_IDENT})\s*(?:PARTITION\s*\([^)]*\)\s*)?(\([^()]*\))?\s*(.*)$",
    re.IGNORECASE | re.DOTALL)
_ALTER_FK_RE = re.compile(
    rf"^ALTER\s+TABLE\s+({_IDENT})\s+ADD\s+(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+({_IDENT})\s*\(([^)]*)\)",
    re.IGNORECASE | re.DOTALL)
_JOIN_EQ_RE = re.compile(r"\b(\w+)\s*\.\s*(\w+)\s*=\s*(\w+)\s*\.\s*(\w+)\b")
_QUALIFIED_RE = re.compile(r"\b(\w+)\s*\.\s*(\w+)\b(?!\s*\()")
_FUNC_TYPES = [
    (re.compile(r"^\s*count\s*\(", re.I), "bigint"),
    (re.compile(r"^\s*(sum|avg|round|power|abs|ceil|floor)\s*\(", re.I), "decimal(18,2)"),
    (re.compile(r"^\s*(to_date|date|current_date|date_trunc|last_day|add_months)\b", re.I), "date"),
    (re.compile(r"^\s*(current_timestamp|to_timestamp|now)\b", re.I), "timestamp"),
    (re.compile(r"^\s*(upper|lower|trim|concat|substr|substring|lpad|rpad|coalesce\s*\(\s*'|replace)\b", re.I), "string"),
    (re.compile(r"^\s*cast\s*\(.*\bas\s+([\w(), ]+?)\s*\)\s*$", re.I | re.S), None),
]
_TYPE_WORDS = {"string", "varchar", "char", "int", "integer", "bigint", "smallint", "tinyint", "decimal", "numeric",
               "double", "float", "real", "date", "timestamp", "boolean", "interval", "day", "month", "year"}


# --------------------------------------------------------------------------- découpage


def split_statements(text: str) -> list[str]:
    """Découpe un script en instructions (`;` hors chaînes), commentaires retirés."""
    text = _COMMENT_RE.sub(" ", text)
    statements, current, in_string = [], [], False
    for ch in text:
        if ch == "'":
            in_string = not in_string
        if ch == ";" and not in_string:
            statements.append("".join(current))
            current = []
        else:
            current.append(ch)
    statements.append("".join(current))
    return [" ".join(s.split()) for s in statements if s.strip()]


def _ident(raw: str, default_db: str) -> str:
    """Identifiant `base.nom` en minuscules (base par défaut si absente)."""
    name = re.sub(r"[`\"\[\]\s]", "", raw).lower()
    return name if "." in name else f"{default_db}.{name}"


def _cols(raw: str) -> list[str]:
    return [re.sub(r"[`\"\[\]\s]", "", c).lower() for c in raw.split(",") if c.strip()]


def _paren_block(text: str) -> tuple[str, str]:
    """Contenu de la première parenthèse équilibrée de `text`, et la suite."""
    start = text.find("(")
    if start < 0:
        return "", text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], text[i + 1:]
    return text[start + 1:], ""


def _main_select(query: str) -> str:
    """Dernier SELECT de premier niveau (ignore les CTE `WITH x AS (...)`)."""
    depth, last = 0, -1
    for match in re.finditer(r"\(|\)|\bSELECT\b", query, re.IGNORECASE):
        token = match.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0:
            last = match.start()
    return query[last:] if last >= 0 else query


# --------------------------------------------------------------------------- modèle


class _Model:
    """Accumule objets, flux et relations au fil des instructions."""

    def __init__(self, default_db: str):
        self.default_db = default_db
        self.objects: "OrderedDict[str, dict]" = OrderedDict()
        self.declared: set[str] = set()          # objets décrits par un DDL ou une vue
        self.flows: list[dict] = []
        self.relations: dict[tuple, dict] = {}
        self.warnings: list[str] = []

    def obj(self, obj_id: str, otype: str = "TABLE", description: str = "") -> dict:
        if obj_id not in self.objects:
            database, name = obj_id.split(".", 1)
            self.objects[obj_id] = {"id": obj_id, "database": database, "name": name, "type": otype,
                                    "description": description, "columns": OrderedDict()}
        return self.objects[obj_id]

    def add_column(self, obj_id: str, name: str, ctype: str | None = None, is_key: bool = False,
                   description: str = "") -> None:
        cols = self.obj(obj_id)["columns"]
        if not name:
            return
        col = cols.setdefault(name, {"name": name, "type": None, "is_key": False, "description": ""})
        col["type"] = col["type"] or ctype
        col["is_key"] = col["is_key"] or is_key
        col["description"] = col["description"] or description

    def add_relation(self, a: str, ac: str, b: str, bc: str, comment: str) -> None:
        if a == b:
            return
        if (b, bc) < (a, ac):
            a, ac, b, bc = b, bc, a, ac
        self.relations.setdefault((a, ac, b, bc), {"a": a, "a_column": ac, "b": b, "b_column": bc, "comment": comment})

    def column_type(self, obj_id: str, col: str) -> str | None:
        obj = self.objects.get(obj_id)
        return obj["columns"].get(col, {}).get("type") if obj else None

    def to_metadata(self, source: str) -> dict:
        objects = []
        for obj in self.objects.values():
            if obj["id"] not in self.declared and not obj["description"]:
                obj["description"] = "Structure déduite des requêtes SQL (table non déclarée)"
            objects.append({**obj, "columns": [{**c, "position": i} for i, c in enumerate(obj["columns"].values(), 1)]})
        return {"meta": {"format_version": "1.0", "source": source, "extractor": "engine.sql_ingest",
                         "warnings": self.warnings[:200]},
                "objects": objects, "flows": self.flows, "relations": list(self.relations.values())}


# --------------------------------------------------------------------------- DDL


def _ctas_query(rest: str) -> tuple[str | None, bool]:
    """(requête du CREATE TABLE ... AS SELECT, vrai si des colonnes sont déclarées avant)."""
    match = re.search(r"\bAS\s+(\(?\s*(?:SELECT|WITH)\b.*)$", rest, re.IGNORECASE | re.DOTALL)
    if not match:
        return None, True
    return match.group(1), "(" in rest[:match.start()]


def _parse_create_table(model: _Model, obj_id: str, rest: str) -> None:
    query, has_columns = _ctas_query(rest)
    if query is not None and not has_columns:
        return
    body, after = _paren_block(rest)
    if not body:
        return
    model.declared.add(obj_id)
    model.obj(obj_id, "TABLE")
    comment = re.search(r"\bCOMMENT\s+'((?:[^']|'')*)'", after, re.IGNORECASE)
    if comment:
        model.obj(obj_id)["description"] = comment.group(1).replace("''", "'")
    for item in _split_top_level(body):
        _parse_column_def(model, obj_id, item)
    partitions = re.search(r"\bPARTITIONED\s+BY\s*\(", after, re.IGNORECASE)
    if partitions:
        part_body, _ = _paren_block(after[partitions.start():])
        for item in _split_top_level(part_body):
            _parse_column_def(model, obj_id, item)


def _parse_column_def(model: _Model, obj_id: str, item: str) -> None:
    pk = re.match(r"^(?:CONSTRAINT\s+\w+\s+)?PRIMARY\s+KEY\s*\(([^)]*)\)", item, re.IGNORECASE)
    if pk:
        for col in _cols(pk.group(1)):
            model.add_column(obj_id, col, is_key=True)
        return
    fk = re.match(rf"^(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s+({_IDENT})\s*\(([^)]*)\)",
                  item, re.IGNORECASE)
    if fk:
        ref = _ident(fk.group(2), model.default_db)
        for col, ref_col in zip(_cols(fk.group(1)), _cols(fk.group(3))):
            model.add_relation(obj_id, col, ref, ref_col, "Clé étrangère déclarée (DDL)")
        return
    if re.match(r"^(CONSTRAINT|UNIQUE|KEY|INDEX|CHECK)\b", item, re.IGNORECASE):
        return
    col = re.match(r"^([`\"\[]?[\w$]+[`\"\]]?)\s+([\w]+(?:\s*\([^)]*\))?(?:<[^>]*>)?)(.*)$", item, re.DOTALL)
    if not col:
        return
    name = re.sub(r"[`\"\[\]]", "", col.group(1)).lower()
    ctype, extra = col.group(2).lower().replace(" ", ""), col.group(3)
    desc = re.search(r"\bCOMMENT\s+'((?:[^']|'')*)'", extra, re.IGNORECASE)
    model.add_column(obj_id, name, ctype, bool(re.search(r"\bPRIMARY\s+KEY\b", extra, re.IGNORECASE)),
                     desc.group(1).replace("''", "'") if desc else "")
    ref = re.search(rf"\bREFERENCES\s+({_IDENT})\s*\(([^)]*)\)", extra, re.IGNORECASE)
    if ref:
        model.add_relation(obj_id, name, _ident(ref.group(1), model.default_db), _cols(ref.group(2))[0],
                           "Clé étrangère déclarée (DDL)")


# --------------------------------------------------------------------------- requêtes


def _sources(model: _Model, query: str) -> tuple[list[str], dict[str, str]]:
    """Tables lues (FROM / JOIN, hors CTE) et dictionnaire alias → table."""
    text = _strip(query)
    ctes = {m.group(1).lower() for m in _CTE_RE.finditer(text)}
    sources, aliases = [], {}
    for match in _SOURCE_RE.finditer(text):
        raw = match.group(1)
        if raw.lower() in ctes or raw.lower() in ("select", "lateral"):
            continue
        obj_id = _ident(raw, model.default_db)
        if obj_id not in sources:
            sources.append(obj_id)
        model.obj(obj_id)
        alias = (match.group(2) or "").lower()
        if alias and alias not in {"where", "group", "order", "join", "left", "right", "inner", "on", "full",
                                   "cross", "limit", "having", "union", "lateral", "outer", "using"}:
            aliases[alias] = obj_id
        aliases[obj_id.split(".")[-1]] = obj_id
        aliases[obj_id] = obj_id
    return sources, aliases


def _learn_usage(model: _Model, query: str, sources: list[str], aliases: dict[str, str]) -> None:
    """Colonnes référencées (`alias.col`) → colonnes des tables lues ; `a.x = b.y` → relations."""
    text = _strip(query)
    for qualifier, col in _QUALIFIED_RE.findall(text):
        target = aliases.get(qualifier.lower())
        if target and target not in model.declared and col.lower() not in _TYPE_WORDS:
            model.add_column(target, col.lower())
    for qa, ca, qb, cb in _JOIN_EQ_RE.findall(text):
        a, b = aliases.get(qa.lower()), aliases.get(qb.lower())
        if a and b:
            model.add_relation(a, ca.lower(), b, cb.lower(), "Jointure lue dans une requête SQL")


def _expression_type(expression: str, refs: list[dict], model: _Model) -> str | None:
    for pattern, ctype in _FUNC_TYPES:
        match = pattern.search(expression)
        if match:
            return ctype or match.group(1).lower().replace(" ", "")
    if len(refs) == 1:
        return model.column_type(refs[0]["source_object"], refs[0]["source"])
    return None


def _parse_query(model: _Model, query: str, target: str | None, target_cols: list[str] | None,
                 otype: str, job: str) -> None:
    """Analyse un SELECT : structure des sources, colonnes de la cible, flux colonne à colonne."""
    sources, aliases = _sources(model, query)
    _learn_usage(model, query, sources, aliases)
    if target is None:
        return
    is_new = target not in model.declared
    if not is_new and not target_cols and model.objects[target]["columns"]:
        target_cols = list(model.objects[target]["columns"])     # INSERT positionnel dans une table déclarée
    model.obj(target, otype)
    if otype == "VIEW" or is_new:
        model.declared.add(target)
    main = _strip(_main_select(query))
    select = re.match(r"^SELECT\s+(?:DISTINCT\s+)?(.*?)\s+FROM\b", main, re.IGNORECASE | re.DOTALL)
    items = _split_top_level(select.group(1)) if select else []
    mappings: dict[str, list[dict]] = {}
    position = 0
    for item in items:
        star = re.match(r"^(?:(\w+)\.)?\*$", item.strip())
        if star:
            expanded = [aliases[star.group(1).lower()]] if star.group(1) and star.group(1).lower() in aliases else sources
            for src in expanded:
                for col in list(model.objects[src]["columns"].values()):
                    name = _target_name(target_cols, position, col["name"])
                    position += 1
                    model.add_column(target, name, col["type"], col["is_key"])
                    mappings.setdefault(src, []).append({"source": col["name"], "target": name, "transformation": None})
            continue
        alias = re.search(r"\s+(?:AS\s+)?([`\"]?\w+[`\"]?)\s*$", item, re.IGNORECASE)
        if alias and (_unquote(alias.group(1)) in _RESERVED
                      or re.search(r"[+\-*/%|=<>(,]\s*$", item[:alias.start()])):
            alias = None
        expression = item[:alias.start()] if alias else item
        plain = [r for r in _parse_select_item(expression, aliases, sources) if r["source"] not in _TYPE_WORDS]
        if target_cols and position < len(target_cols):
            name = target_cols[position]                  # INSERT : colonnes de la cible, par position
        elif alias:
            name = _unquote(alias.group(1))
        elif len(plain) == 1 and plain[0]["target"]:
            name = plain[0]["target"]
        else:
            name = f"expr_{position + 1}"
        position += 1
        refs = [r for r in _parse_select_item(f"{expression} AS {name}", aliases, sources) if r["source"] not in _TYPE_WORDS]
        source_key = (len(refs) == 1 and re.fullmatch(r"\s*(?:\w+\.)?\w+\s*|\s*(upper|lower|trim)\s*\(.*\)\s*", expression, re.I)
                      and model.objects.get(refs[0]["source_object"], {}).get("columns", {}).get(refs[0]["source"], {}).get("is_key"))
        model.add_column(target, name, _expression_type(expression, refs, model), bool(source_key))
        seen = set()
        for ref in refs:
            if ref["source_object"] not in sources or (ref["source_object"], ref["source"]) in seen:
                continue
            seen.add((ref["source_object"], ref["source"]))
            if ref["source_object"] not in model.declared:
                model.add_column(ref["source_object"], ref["source"])
            mappings.setdefault(ref["source_object"], []).append(
                {"source": ref["source"], "target": name, "transformation": ref["transformation"]})
    for src in sources:
        if src == target:
            continue
        model.flows.append({"source": src, "target": target, "job": job, "origin": SQL_ORIGIN,
                            "column_mapping": mappings.get(src, [])})


def _target_name(target_cols: list[str] | None, position: int, default: str) -> str:
    if target_cols and position < len(target_cols):
        return target_cols[position]
    return default


# --------------------------------------------------------------------------- point d'entrée


def parse_sql_script(text: str, default_database: str = "default", source: str = "sql") -> dict:
    """Analyse un script SQL complet et renvoie des métadonnées au format du contrat JSON."""
    model = _Model(default_database.lower())
    statements = split_statements(text)
    # 1er passage : DDL (structure explicite, prioritaire sur ce qui est déduit des requêtes).
    for stmt in statements:
        match = _CREATE_TABLE_RE.match(stmt)
        if match:
            _parse_create_table(model, _ident(match.group(1), model.default_db), match.group(2))
    # 2e passage : vues, CTAS, INSERT, SELECT, ALTER.
    for n, stmt in enumerate(statements, 1):
        job = f"requete_{n}"
        view = _CREATE_VIEW_RE.match(stmt)
        if view:
            _parse_query(model, view.group(3), _ident(view.group(1), model.default_db),
                         _cols(view.group(2)[1:-1]) if view.group(2) else None, "VIEW", job)
            continue
        table = _CREATE_TABLE_RE.match(stmt)
        if table:
            query, _ = _ctas_query(table.group(2))
            if query:
                _parse_query(model, query, _ident(table.group(1), model.default_db), None, "TABLE", job)
            continue
        insert = _INSERT_RE.match(stmt)
        if insert:
            if re.match(r"^\(?\s*(SELECT|WITH)\b", insert.group(3), re.IGNORECASE):
                _parse_query(model, insert.group(3), _ident(insert.group(1), model.default_db),
                             _cols(insert.group(2)[1:-1]) if insert.group(2) else None, "TABLE", job)
            continue
        fk = _ALTER_FK_RE.match(stmt)
        if fk:
            a, b = _ident(fk.group(1), model.default_db), _ident(fk.group(3), model.default_db)
            for ac, bc in zip(_cols(fk.group(2)), _cols(fk.group(4))):
                model.add_relation(a, ac, b, bc, "Clé étrangère déclarée (ALTER TABLE)")
            continue
        if re.match(r"^\(?\s*(SELECT|WITH)\b", stmt, re.IGNORECASE):
            _parse_query(model, stmt, None, None, "TABLE", job)
            continue
        if not re.match(r"^(USE|SET|DROP|TRUNCATE|ANALYZE|MSCK|COMMENT|GRANT|REFRESH|INVALIDATE)\b", stmt, re.IGNORECASE):
            model.warnings.append(f"Instruction {n} ignorée : {stmt[:80]}…")
    return model.to_metadata(source)
