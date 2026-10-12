"""Écriture d'un classeur Excel (.xlsx) minimal avec la bibliothèque standard (zipfile + XML).

Suffisant pour exporter des tableaux : une ligne d'en-tête en gras, du texte et des nombres,
un filtre automatique et la première ligne figée. Point d'entrée : `write_xlsx(sheets)` → bytes,
où `sheets` = liste de (nom_onglet, en-têtes, lignes).
"""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


def _col(n: int) -> str:
    name = ""
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def _cell(ref: str, value, style: int = 0) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        value = "oui" if value else "non"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    text = escape(str(value)).replace("\n", "&#10;")
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(headers: list[str], rows: list[list]) -> str:
    widths = [max([len(str(h))] + [len(str(r[i])) if i < len(r) and r[i] is not None else 0 for r in rows[:500]])
              for i, h in enumerate(headers)]
    cols = "".join(f'<col min="{i}" max="{i}" width="{min(70, max(10, w + 2))}" customWidth="1"/>'
                   for i, w in enumerate(widths, 1))
    lines = ['<row r="1">' + "".join(_cell(f"{_col(j)}1", h, 1) for j, h in enumerate(headers, 1)) + "</row>"]
    for i, row in enumerate(rows, 2):
        lines.append(f'<row r="{i}">' + "".join(_cell(f"{_col(j)}{i}", v) for j, v in enumerate(row, 1)) + "</row>")
    last = f"{_col(max(1, len(headers)))}{max(1, len(rows) + 1)}"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            'state="frozen"/></sheetView></sheetViews>'
            f'<cols>{cols}</cols><sheetData>{"".join(lines)}</sheetData><autoFilter ref="A1:{last}"/></worksheet>')


def write_xlsx(sheets: list[tuple[str, list[str], list[list]]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        names = [escape(name[:31]) for name, _, _ in sheets]
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" '
                   'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                             'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1)) + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
                   'officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                             f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/'
                     '2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="10"/><name val="Arial"/></font>'
                   '<font><b/><sz val="10"/><name val="Arial"/></font></fonts>'
                   '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                   '<fill><patternFill patternType="gray125"/></fill></fills>'
                   '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="2"><xf fontId="0"/><xf fontId="1" applyFont="1"/></cellXfs></styleSheet>')
        for i, (_, headers, rows) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet(headers, rows))
    return buffer.getvalue()
