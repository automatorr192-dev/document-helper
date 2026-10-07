import io
import re
import zipfile
from xml.sax.saxutils import escape

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
FONT = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
CLAUSE = re.compile(r"^\s*(\d+(?:\.\d+)+)\.?\s")
WIDTHS = (600, 1700, 4300, 4300, 4300)


def _run(text: str, bold: bool = False, size: int = 22) -> str:
    props = f'{FONT}{"<w:b/>" if bold else ""}<w:sz w:val="{size}"/>'
    lines = escape(text).split("\n")
    body = "<w:br/>".join(f'<w:t xml:space="preserve">{line}</w:t>' for line in lines)
    return f"<w:r><w:rPr>{props}</w:rPr>{body}</w:r>"


def _p(text: str, bold: bool = False, align: str = "left", size: int = 22, after: int = 120) -> str:
    return (
        f'<w:p><w:pPr><w:spacing w:after="{after}"/><w:jc w:val="{align}"/></w:pPr>'
        f"{_run(text, bold, size)}</w:p>"
    )


def _cell(text: str, width: int, bold: bool = False) -> str:
    shade = '<w:shd w:val="clear" w:color="auto" w:fill="EDEDED"/>' if bold else ""
    return (
        f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{shade}</w:tcPr>'
        f"{_p(text, bold=bold, size=20, after=0)}</w:tc>"
    )


def _row(cells: list[str], bold: bool = False) -> str:
    header = "<w:trPr><w:tblHeader/></w:trPr>" if bold else ""
    return (
        "<w:tr>"
        + header
        + "".join(_cell(c, w, bold) for c, w in zip(cells, WIDTHS, strict=True))
        + "</w:tr>"
    )


def clause_number(quote: str) -> str:
    match = CLAUSE.match(quote or "")
    return f"п. {match.group(1)}" if match else "—"


def disputed(report: dict) -> list[dict]:
    return [f for f in report.get("findings") or [] if f.get("severity") in ("red", "yellow")]


def docx(report: dict) -> bytes:
    border = '<w:{0} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
    borders = "".join(
        border.format(side) for side in ("top", "left", "bottom", "right", "insideH", "insideV")
    )
    rows = [
        _row(["№", "Пункт", "Редакция договора", "Предлагаемая редакция", "Обоснование"], bold=True)
    ]
    for index, finding in enumerate(disputed(report), start=1):
        reason = finding.get("basis") or finding.get("plain") or ""
        if finding.get("article"):
            reason += f" ({finding['article']})"
        rows.append(
            _row(
                [
                    str(index),
                    clause_number(finding.get("quote", "")),
                    finding.get("quote") or "",
                    finding.get("fix") or "Исключить пункт.",
                    reason.strip(),
                ]
            )
        )
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in WIDTHS)
    table = (
        f'<w:tbl><w:tblPr><w:tblW w:w="{sum(WIDTHS)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
        f"<w:tblBorders>{borders}</w:tblBorders></w:tblPr><w:tblGrid>{grid}</w:tblGrid>"
        + "".join(rows)
        + "</w:tbl>"
    )
    sign = (
        '<w:tbl><w:tblPr><w:tblW w:w="15200" w:type="dxa"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="7600"/><w:gridCol w:w="7600"/></w:tblGrid><w:tr>'
        + "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="7600" w:type="dxa"/></w:tcPr>'
            f"{_p(side, bold=True)}{_p('____________________ / ____________')}{_p('М.П.')}</w:tc>"
            for side in ("Сторона 1", "Сторона 2")
        )
        + "</w:tr></w:tbl>"
    )
    body = (
        _p("ПРОТОКОЛ РАЗНОГЛАСИЙ", bold=True, align="center", size=28)
        + _p("к договору № ______ от «___» ____________ 20___ г.", align="center")
        + _p("г. ____________, «___» ____________ 20___ г.")
        + _p(
            "Стороны договорились изложить перечисленные ниже пункты договора в следующей "
            "редакции. Остальные условия договора остаются без изменений."
        )
        + table
        + _p("")
        + _p(
            "Протокол является неотъемлемой частью договора. При расхождении текста договора "
            "и протокола применяется редакция протокола."
        )
        + sign
    )
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>{body}'
        '<w:sectPr><w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
        '<w:pgMar w:top="850" w:right="850" w:bottom="850" w:left="850" w:header="0" w:footer="0" w:gutter="0"/>'
        "</w:sectPr></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()
