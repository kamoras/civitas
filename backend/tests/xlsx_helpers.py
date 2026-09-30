"""Build minimal real .xlsx payloads for the state-results reader tests.

Shared by the tabular, Maine, New Hampshire and Wyoming tests, which all
feed hand-built workbooks to an `_xlsx_rows` reader. Not a test module, so
nothing here is collected.
"""

import io
import zipfile


def _col_letter(i: int) -> str:
    """0-based column index -> spreadsheet column letters (0 -> "A", 26 -> "AA")."""
    letters = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _workbook(rows: list[list[str | None]]) -> bytes:
    """Minimal real .xlsx: a zip of the two XML parts _xlsx_rows reads,
    with every cell a shared-string reference (t="s") and a real column
    reference (r="A1", "B1", ...) — both how the California workbook
    actually encodes its text. A `None` entry OMITS that cell from the
    row entirely, the real shape a spreadsheet writer produces for a
    wholly blank interior cell (Maine's real per-town exports do this —
    e.g. a UOCAVA summary row with no county name) — exactly the case
    _xlsx_rows' own r=-based column placement exists to survive."""
    table = []
    for row in rows:
        for cell in row:
            if cell is not None and cell not in table:
                table.append(cell)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    shared = f"<sst {ns}>" + "".join(f"<si><t>{v}</t></si>" for v in table) + "</sst>"
    body = "".join(
        "<row>" + "".join(
            f'<c r="{_col_letter(i)}{rownum}" t="s"><v>{table.index(cell)}</v></c>'
            for i, cell in enumerate(row) if cell is not None
        ) + "</row>"
        for rownum, row in enumerate(rows, start=1)
    )
    sheet = f"<worksheet {ns}><sheetData>{body}</sheetData></worksheet>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/sharedStrings.xml", shared)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()
