"""Convert docs/엔진_설명.md to a styled PDF using reportlab + Malgun Gothic.

Uses Malgun Gothic TTF (bundled with Windows) embedded into the PDF so
Korean glyphs render on any viewer. Saves to Desktop as
'춘큐 스탁 어드바이져 - 엔진 설명서.pdf'.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import markdown as md_lib
from bs4 import BeautifulSoup, NavigableString
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MD_PATH = PROJECT_ROOT / "docs" / "엔진_설명.md"

DESKTOP_CANDIDATES = [
    Path.home() / "OneDrive" / "바탕 화면",
    Path.home() / "OneDrive" / "Desktop",
    Path.home() / "Desktop",
    Path.home() / "바탕 화면",
]

FONT_REG = "C:/Windows/Fonts/malgun.ttf"
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"


def find_desktop() -> Path:
    for p in DESKTOP_CANDIDATES:
        if p.exists():
            return p
    return Path.home()


def register_fonts() -> tuple[str, str]:
    if not Path(FONT_REG).exists():
        raise FileNotFoundError(f"Korean font not found: {FONT_REG}")
    pdfmetrics.registerFont(TTFont("Malgun", FONT_REG))
    if Path(FONT_BOLD).exists():
        pdfmetrics.registerFont(TTFont("MalgunBold", FONT_BOLD))
        pdfmetrics.registerFontFamily(
            "Malgun", normal="Malgun", bold="MalgunBold", italic="Malgun", boldItalic="MalgunBold",
        )
    else:
        pdfmetrics.registerFontFamily("Malgun", normal="Malgun")
    return "Malgun", "MalgunBold"


def build_styles(font: str, font_bold: str) -> dict:
    s = {}
    s["body"] = ParagraphStyle(
        "body", fontName=font, fontSize=10.5, leading=16,
        textColor=colors.HexColor("#1a2135"), spaceAfter=4,
    )
    s["h1"] = ParagraphStyle(
        "h1", fontName=font_bold, fontSize=20, leading=26,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=6, spaceAfter=10, borderPadding=2,
    )
    s["h2"] = ParagraphStyle(
        "h2", fontName=font_bold, fontSize=14, leading=20,
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=16, spaceAfter=6,
    )
    s["h3"] = ParagraphStyle(
        "h3", fontName=font_bold, fontSize=12, leading=17,
        textColor=colors.HexColor("#1e3a8a"),
        spaceBefore=12, spaceAfter=4,
    )
    s["h4"] = ParagraphStyle(
        "h4", fontName=font_bold, fontSize=11, leading=15,
        textColor=colors.HexColor("#334155"),
        spaceBefore=8, spaceAfter=2,
    )
    s["li"] = ParagraphStyle(
        "li", fontName=font, fontSize=10.5, leading=15,
        leftIndent=14, bulletIndent=4, spaceAfter=1,
        textColor=colors.HexColor("#1a2135"),
    )
    s["code_block"] = ParagraphStyle(
        "code_block", fontName="Courier", fontSize=9.2, leading=13,
        textColor=colors.HexColor("#e5edff"),
        backColor=colors.HexColor("#0f172a"),
        borderPadding=8, leftIndent=4, rightIndent=4,
        spaceBefore=6, spaceAfter=8,
    )
    s["quote"] = ParagraphStyle(
        "quote", fontName=font, fontSize=10.5, leading=15,
        textColor=colors.HexColor("#064e3b"),
        backColor=colors.HexColor("#ecfdf5"),
        leftIndent=10, borderPadding=6,
        spaceBefore=4, spaceAfter=6,
    )
    return s


def inline_html(tag) -> str:
    """Convert a BS4 tag's inline content (children) to reportlab-safe rich text."""
    parts = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            parts.append(str(child).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        else:
            nm = child.name
            inner = inline_html(child)
            if nm in ("strong", "b"):
                parts.append(f"<b>{inner}</b>")
            elif nm in ("em", "i"):
                parts.append(f"<i>{inner}</i>")
            elif nm == "code":
                parts.append(
                    f'<font face="Courier" color="#b91c1c" backColor="#f1f5f9">{inner}</font>'
                )
            elif nm == "a":
                href = child.get("href", "")
                parts.append(f'<link href="{href}" color="#2563eb">{inner}</link>')
            elif nm == "br":
                parts.append("<br/>")
            else:
                parts.append(inner)
    return "".join(parts).strip()


def build_table_from_html(tag, styles: dict, font: str, font_bold: str) -> Table:
    rows = []
    for tr in tag.find_all("tr"):
        row = []
        for cell in tr.find_all(["th", "td"]):
            p = Paragraph(inline_html(cell), styles["body"])
            row.append(p)
        if row:
            rows.append(row)
    if not rows:
        return None
    n_cols = max(len(r) for r in rows)
    col_w = (A4[0] - 36 * mm) / n_cols
    tbl = Table(rows, colWidths=[col_w] * n_cols)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#e5edff")),
        ("FONTNAME", (0, 0), (-1, 0), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f8fafc")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tbl


def md_to_flowables(md_text: str, styles: dict, font: str, font_bold: str) -> list:
    html = md_lib.markdown(md_text, extensions=["tables", "fenced_code", "nl2br"])
    soup = BeautifulSoup(html, "lxml")

    flowables = []
    body = soup.body or soup
    for el in body.children:
        if isinstance(el, NavigableString):
            txt = str(el).strip()
            if txt:
                flowables.append(Paragraph(txt.replace("\n", "<br/>"), styles["body"]))
            continue

        name = el.name
        if name == "h1":
            flowables.append(Paragraph(inline_html(el), styles["h1"]))
            flowables.append(HRFlowable(width="100%", thickness=1.2,
                                        color=colors.HexColor("#22c55e"),
                                        spaceBefore=0, spaceAfter=8))
        elif name == "h2":
            flowables.append(Paragraph(inline_html(el), styles["h2"]))
        elif name == "h3":
            flowables.append(Paragraph(inline_html(el), styles["h3"]))
        elif name == "h4":
            flowables.append(Paragraph(inline_html(el), styles["h4"]))
        elif name == "p":
            txt = inline_html(el)
            if txt:
                flowables.append(Paragraph(txt, styles["body"]))
        elif name in ("ul", "ol"):
            items = el.find_all("li", recursive=False)
            for idx, li in enumerate(items, start=1):
                bullet = "•" if name == "ul" else f"{idx}."
                flowables.append(Paragraph(f"{bullet}  {inline_html(li)}", styles["li"]))
        elif name == "pre":
            code = el.get_text()
            # reportlab Paragraph preserves <br/> for newlines
            safe = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            safe = safe.replace("\n", "<br/>")
            flowables.append(Paragraph(safe, styles["code_block"]))
        elif name == "blockquote":
            flowables.append(Paragraph(inline_html(el), styles["quote"]))
        elif name == "hr":
            flowables.append(HRFlowable(width="100%", thickness=0.6,
                                        color=colors.HexColor("#d0d8e6"),
                                        spaceBefore=8, spaceAfter=8))
        elif name == "table":
            tbl = build_table_from_html(el, styles, font, font_bold)
            if tbl is not None:
                flowables.append(Spacer(1, 4))
                flowables.append(tbl)
                flowables.append(Spacer(1, 6))
    return flowables


def build_pdf(out_path: Path, md_text: str) -> None:
    font, font_bold = register_fonts()
    styles = build_styles(font, font_bold)
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=20 * mm, bottomMargin=18 * mm,
        title="춘큐 스탁 어드바이져 - 엔진 설명서",
        author="춘큐 스탁 어드바이져",
    )
    flows = md_to_flowables(md_text, styles, font, font_bold)
    doc.build(flows)


def main() -> None:
    md_text = MD_PATH.read_text(encoding="utf-8")
    desktop = find_desktop()
    out_path = desktop / "춘큐 스탁 어드바이져 - 엔진 설명서.pdf"
    build_pdf(out_path, md_text)
    print(f"PDF saved to: {out_path}")

    doc_copy = PROJECT_ROOT / "docs" / "엔진_설명.pdf"
    build_pdf(doc_copy, md_text)
    print(f"copy at: {doc_copy}")


if __name__ == "__main__":
    main()
