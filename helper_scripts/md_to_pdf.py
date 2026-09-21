#!/usr/bin/env python3
"""Render a Markdown document to a clean A4 PDF with ReportLab.

Used for the planning and review documents that live beside the code, so the
PDF stays reproducible from its Markdown source:

    python3 helper_scripts/md_to_pdf.py IMPROVEMENT_PLAN.md Equiper_Improvement_Plan.pdf

Supported Markdown: headings (#, ##, ###), paragraphs, bullet lists (-, *),
numbered lists, checklists (- [ ]), tables (| a | b |, with \\| for a literal
pipe), fenced blocks (```), display maths ($$ ... $$, typeset via matplotlib
mathtext), horizontal rules (---), inline **bold**, *italic* and `code`. Anything else is rendered as plain text, which keeps the converter small and predictable
rather than complete.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from PIL import Image as PILImage
from reportlab.platypus import (
    HRFlowable,
    Image,
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6b7280")
ACCENT = colors.HexColor("#15803d")
RULE = colors.HexColor("#d1d5db")
HEAD_BG = colors.HexColor("#f3f4f6")
CODE_BG = colors.HexColor("#f9fafb")

PAGE_MARGIN = 18 * mm
CONTENT_WIDTH = A4[0] - 2 * PAGE_MARGIN


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()["BodyText"]
    body = ParagraphStyle(
        "Body", parent=base, fontName="Helvetica", fontSize=9.5, leading=14,
        textColor=INK, alignment=TA_LEFT, spaceAfter=6,
    )
    return {
        "body": body,
        "h1": ParagraphStyle("H1", parent=body, fontName="Helvetica-Bold", fontSize=20,
                             leading=24, textColor=INK, spaceBefore=0, spaceAfter=10),
        "h2": ParagraphStyle("H2", parent=body, fontName="Helvetica-Bold", fontSize=13,
                             leading=17, textColor=INK, spaceBefore=16, spaceAfter=6),
        "h3": ParagraphStyle("H3", parent=body, fontName="Helvetica-Bold", fontSize=10.5,
                             leading=14, textColor=ACCENT, spaceBefore=11, spaceAfter=4),
        "cell": ParagraphStyle("Cell", parent=body, fontSize=8.5, leading=11.5, spaceAfter=0),
        "cellhead": ParagraphStyle("CellHead", parent=body, fontName="Helvetica-Bold",
                                   fontSize=8.5, leading=11.5, spaceAfter=0),
        "item": ParagraphStyle("Item", parent=body, spaceAfter=3),
        "check": ParagraphStyle("Check", parent=body, leftIndent=16, firstLineIndent=-16,
                                spaceAfter=3),
        "code": ParagraphStyle("Code", parent=body, fontName="Courier", fontSize=7.8,
                               leading=10.5, textColor=INK, spaceAfter=0),
    }


# Glyphs the Helvetica/Courier standard encodings lack, which otherwise render as
# a filled square. Spelled out rather than substituted with a lookalike so a
# formula stays readable.
MISSING_GLYPHS = {
    "̄": "",       # combining macron: x̄ arrives as "x" + this
    "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3",
    "ᵢ": "_i", "ⱼ": "_j", "ₙ": "_n",
    "√": "sqrt", "ν": "nu", "σ": "sigma",
}


def defont(text: str) -> str:
    """Replace characters absent from the built-in font encodings."""
    for bad, good in MISSING_GLYPHS.items():
        text = text.replace(bad, good)
    return text


def _inline_math_tag(expr: str) -> str:
    """One inline equation as a Paragraph `<img>`, sitting on the text baseline.

    Inline maths has to flow with the words around it, so it cannot be a
    separate flowable. ReportLab's Paragraph accepts `<img>`, which lets a
    typeset fragment sit in the middle of a sentence at the right size.
    """
    try:
        path, w, h = render_math(expr, inline=True)
    except Exception:
        return f"<i>{expr}</i>"
    # A small negative rise drops the image to the text baseline; without it
    # the fragment floats above the line it belongs to.
    return f'<img src="{path}" width="{w:.2f}" height="{h:.2f}" valign="-2"/>'


def inline(text: str) -> str:
    """Markdown inline markup to ReportLab's mini-HTML, escaping first."""
    out = defont(text)
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = out.replace("\\|", "|")
    out = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8.5">\1</font>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\w)", r"<i>\1</i>", out)
    # Inline maths last: the expression must not be mangled by the markup
    # rules above, and its `<img>` must not be escaped.
    #
    # ReportLab trims whitespace next to a tag, so "width $d$, and" rendered as
    # "width d, and" with the spaces gone. The spaces are re-inserted as
    # non-breaking ones, which the paragraph parser keeps.
    def _math_sub(m):
        tag = _inline_math_tag(m.group(1))
        before = out[m.start() - 1] if m.start() else ""
        after = out[m.end()] if m.end() < len(out) else ""
        lead = "&nbsp;" if before.isspace() else ""
        trail = "&nbsp;" if after.isspace() else ""
        return f"{lead}{tag}{trail}"

    out = re.sub(r"(?<!\$)\$([^$\n]+)\$(?!\$)", _math_sub, out)
    return out


# ── Typeset mathematics ──────────────────────────────────────────────────────
#
# The standard PDF fonts have no radical sign, no fraction bar and no summation
# symbol, so formulas previously had to be written as `sqrt(...)` inside a
# Courier block. That is readable but it is not mathematics, and a calibration
# handbook that cannot show a square root properly undersells the material.
#
# matplotlib's mathtext typesets a LaTeX subset without needing a TeX install,
# which is already a dependency here. Each display equation is rendered once to
# a transparent PNG at print resolution and embedded, so the PDF carries real
# notation.

MATH_DPI = 600               # print resolution; placed at natural size
MATH_FONT_PT = 17            # display size; inline is scaled down from this
_MATH_CACHE: dict[str, tuple[str, float, float]] = {}
_MATH_DIR: Path | None = None


def _math_dir() -> Path:
    """Scratch directory for rendered equations, made once per run."""
    global _MATH_DIR
    if _MATH_DIR is None:
        _MATH_DIR = Path(tempfile.mkdtemp(prefix="mdpdf-math-"))
    return _MATH_DIR


def render_math(expr: str, *, inline: bool = False) -> tuple[str, float, float]:
    """Typeset ``expr`` and return (path, width_pt, height_pt).

    Cached by expression: the same formula appearing twice is rendered once.
    """
    key = f"{'i' if inline else 'd'}:{expr}"
    if key in _MATH_CACHE:
        return _MATH_CACHE[key]

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # Inline fragments sit inside 9.5pt body text; display equations stand
    # alone and carry the larger size.
    size = 10.0 if inline else MATH_FONT_PT
    fig = plt.figure(figsize=(0.01, 0.01))
    fig.text(0, 0, f"${expr}$", fontsize=size, color="#111827")

    path = _math_dir() / f"eq{len(_MATH_CACHE):03d}.png"
    fig.savefig(path, dpi=MATH_DPI, transparent=True, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)

    with PILImage.open(path) as img:
        px_w, px_h = img.size
    # Place at natural size: 72 points per inch, MATH_DPI pixels per inch. The
    # extra pixels are what make it crisp; they must not make it large.
    scale = 72.0 / MATH_DPI
    result = (str(path), px_w * scale, px_h * scale)
    _MATH_CACHE[key] = result
    return result


def make_math(expr: str, st: dict):
    """A display equation, centred on its own line."""
    try:
        path, w, h = render_math(expr)
    except Exception as exc:                       # pragma: no cover - font issues
        print(f"  ! could not typeset {expr!r}: {exc}")
        return Paragraph(inline(f"`{expr}`"), st["body"])

    if w > CONTENT_WIDTH:                          # shrink an over-wide equation
        h *= CONTENT_WIDTH / w
        w = CONTENT_WIDTH
    img = Image(path, width=w, height=h)
    img.hAlign = "CENTER"
    return img


def column_widths(rows: list[list[str]]) -> list[float]:
    """Width by longest cell, clamped so no column collapses or hogs the page.

    A column of short labels beside columns of prose gets a small proportional
    share, which was narrow enough to break its labels mid-word. So each column
    also claims at least the width of its longest single word: Paragraph wraps
    between words but hyphenates nothing, and a mid-word break reads as a typo.
    """
    cols = len(rows[0])
    longest = [max(len(r[i]) for r in rows) for i in range(cols)]
    total = sum(longest) or 1
    floor = 0.10 if cols > 2 else 0.18

    # Longest word per column, measured rather than estimated: a character
    # average is too low for bold text, which is what a header row and an
    # emphasised first column both are, so words still broke. `stringWidth`
    # asks the font. 12pt is the cell padding make_table applies.
    def widest_word(col):
        longest_pt = 0.0
        for row in rows:
            for word in re.sub(r"[*`]", "", row[col]).split():
                for font in ("Helvetica-Bold", "Helvetica"):
                    longest_pt = max(longest_pt, stringWidth(word, font, 8.5))
        return longest_pt + 12

    word_pt = [widest_word(i) for i in range(cols)]
    word_share = [min(0.40, p / CONTENT_WIDTH) for p in word_pt]

    shares = [max(floor, word_share[i], longest[i] / total) for i in range(cols)]
    scale = sum(shares)
    widths = [CONTENT_WIDTH * s / scale for s in shares]

    # Normalising by the total can pull a column back under the width its
    # longest word needs, which is how "Sub-parameter" still broke mid-word
    # after being given a share. Pin any column that lands short to its
    # requirement, then take the difference back from the columns that have
    # room to give, in proportion to their surplus.
    required = [min(p, CONTENT_WIDTH * 0.45) for p in word_pt]
    short = [i for i in range(cols) if widths[i] < required[i]]
    if short:
        deficit = sum(required[i] - widths[i] for i in short)
        donors = [i for i in range(cols) if i not in short and widths[i] > required[i]]
        surplus = sum(widths[i] - required[i] for i in donors)
        if surplus > deficit:
            for i in short:
                widths[i] = required[i]
            for i in donors:
                widths[i] -= deficit * (widths[i] - required[i]) / surplus
    return widths


def make_table(rows: list[list[str]], st: dict) -> Table:
    head, *body = rows
    data = [[Paragraph(inline(c), st["cellhead"]) for c in head]]
    data += [[Paragraph(inline(c), st["cell"]) for c in r] for r in body]
    table = Table(data, colWidths=column_widths(rows), repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#e5e7eb")),
        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def make_code(block: list[str], st: dict) -> Table:
    """A fenced block as boxed, tinted, verbatim Courier."""
    body = Preformatted(defont("\n".join(block)), st["code"])
    table = Table([[body]], colWidths=[CONTENT_WIDTH], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), CODE_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def parse(md: str, st: dict) -> list:
    flow: list = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Display maths: $$ ... $$ on its own lines, typeset rather than
        # approximated in Courier.
        if stripped.startswith("$$"):
            body = stripped[2:].strip()
            if body.endswith("$$"):          # single-line $$ x $$
                body = body[:-2].strip()
                i += 1
            else:
                i += 1
                lines_out = [body] if body else []
                while i < len(lines) and not lines[i].strip().endswith("$$"):
                    lines_out.append(lines[i].strip())
                    i += 1
                if i < len(lines):
                    tail = lines[i].strip()[:-2].strip()
                    if tail:
                        lines_out.append(tail)
                    i += 1
                body = " ".join(part for part in lines_out if part)
            if body:
                flow += [Spacer(1, 6), make_math(body, st), Spacer(1, 10)]
            continue

        # Fenced block: kept verbatim in Courier, so formulas and code keep their
        # alignment. Boxed in a one-cell table to survive a page break as a unit.
        if stripped.startswith("```"):
            i += 1
            block = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i].rstrip())
                i += 1
            i += 1  # closing fence
            while block and not block[0].strip():
                block.pop(0)
            while block and not block[-1].strip():
                block.pop()
            if block:
                flow += [Spacer(1, 2), make_code(block, st), Spacer(1, 8)]
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = lines[i].strip().strip("|")
                cells = [c.strip() for c in re.split(r"(?<!\\)\|", row)]
                if not re.match(r"^[\s:|-]+$", "".join(cells)):
                    rows.append(cells)
                i += 1
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            flow += [Spacer(1, 4), make_table(rows, st), Spacer(1, 8)]
            continue

        if re.match(r"^(-{3,}|\*{3,})$", stripped):
            flow += [Spacer(1, 4), HRFlowable(width="100%", thickness=0.6, color=RULE,
                                              spaceBefore=2, spaceAfter=10)]
            i += 1
            continue

        if stripped.startswith("### "):
            flow.append(Paragraph(inline(stripped[4:]), st["h3"]))
            i += 1
            continue
        if stripped.startswith("## "):
            flow.append(Paragraph(inline(stripped[3:]), st["h2"]))
            i += 1
            continue
        if stripped.startswith("# "):
            flow.append(Paragraph(inline(stripped[2:]), st["h1"]))
            i += 1
            continue

        bullet = re.match(r"^([-*]|\d+\.)\s+(.*)$", stripped)
        if bullet:
            items, ordered = [], bool(re.match(r"^\d+\.$", bullet.group(1)))
            while i < len(lines):
                m = re.match(r"^\s*([-*]|\d+\.)\s+(.*)$", lines[i])
                if not m:
                    if lines[i].strip() and lines[i].startswith(("  ", "\t")) and items:
                        items[-1].append(lines[i].strip())  # continuation line
                        i += 1
                        continue
                    break
                items.append([m.group(2)])
                i += 1
            texts = [" ".join(parts) for parts in items]

            # Checklists print as [ ] / [x]: the box glyphs U+2610/U+2611 are absent
            # from Helvetica's encoding and render as filled squares.
            if all(re.match(r"^\[[ xX]\]\s", t) for t in texts):
                for text in texts:
                    mark = "[x]" if text[1] in "xX" else "[ ]"
                    label = re.sub(r"^\[[ xX]\]\s*", "", text)
                    flow.append(Paragraph(
                        f'<font face="Courier" size="8.5">{mark}</font> {inline(label)}',
                        st["check"],
                    ))
                flow.append(Spacer(1, 6))
                continue

            flowed = [ListItem(Paragraph(inline(t), st["item"]), leftIndent=14) for t in texts]
            flow += [ListFlowable(
                flowed,
                bulletType="1" if ordered else "bullet",
                bulletFontSize=9,
                bulletColor=MUTED,
                bulletFormat="%s." if ordered else None,
                start="1" if ordered else None,
                leftIndent=12, spaceBefore=2, spaceAfter=8,
            )]
            continue

        para = [stripped]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
            r"^\s*([-*]|\d+\.|#{1,3}\s|\|)", lines[i]
        ):
            para.append(lines[i].strip())
            i += 1
        flow.append(Paragraph(inline(" ".join(para)), st["body"]))
    return flow


BRAND = "Cirqen Calibration Software"
LOGO_CANDIDATES = (
    Path("static/images/dark.png"),
    Path("static/images/white.png"),
)
LOGO_HEIGHT = 11 * mm            # header mark
WATERMARK_WIDTH = 125 * mm       # centred page mark
WATERMARK_ALPHA = 0.10           # visible as a brand mark, still readable through


def _logo_path() -> Path | None:
    """The brand mark, resolved relative to the repository root."""
    root = Path(__file__).resolve().parent.parent
    for candidate in LOGO_CANDIDATES:
        path = root / candidate
        if path.exists():
            return path
    return None


def _draw_watermark(canvas) -> None:
    """A faint brand mark behind the page content.

    Drawn first so everything else sits on top of it, and at a low alpha: a
    watermark that competes with the text defeats its own purpose. Wrapped in
    its own state so the transparency cannot leak into the header or body.
    """
    logo = _logo_path()
    if logo is None:
        return
    try:
        reader = ImageReader(str(logo))
        iw, ih = reader.getSize()
        width = WATERMARK_WIDTH
        height = width * (ih / iw)
        canvas.saveState()
        canvas.setFillAlpha(WATERMARK_ALPHA)
        canvas.setStrokeAlpha(WATERMARK_ALPHA)
        canvas.drawImage(
            reader,
            (A4[0] - width) / 2,
            (A4[1] - height) / 2,
            width=width, height=height,
            mask="auto", preserveAspectRatio=True,
        )
        canvas.restoreState()
    except Exception:
        pass                                   # a watermark is never worth failing for


def decorate(canvas, doc) -> None:
    _draw_watermark(canvas)
    canvas.saveState()

    # ── Header: brand mark and document title ──
    top = A4[1] - PAGE_MARGIN + 2 * mm
    logo = _logo_path()
    text_x = PAGE_MARGIN
    if logo is not None:
        try:
            reader = ImageReader(str(logo))
            iw, ih = reader.getSize()
            width = LOGO_HEIGHT * (iw / ih)
            canvas.drawImage(
                reader, PAGE_MARGIN, top - LOGO_HEIGHT + 1 * mm,
                width=width, height=LOGO_HEIGHT,
                mask="auto", preserveAspectRatio=True,
            )
            text_x = PAGE_MARGIN + width + 3 * mm
        except Exception:
            pass                                   # a missing logo is not fatal

    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(INK)
    canvas.drawString(text_x, top - 3.2 * mm, BRAND)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(A4[0] - PAGE_MARGIN, top - 3.2 * mm, doc.title or "")

    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(PAGE_MARGIN, top - 6.5 * mm, A4[0] - PAGE_MARGIN, top - 6.5 * mm)

    # ── Footer ──
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(PAGE_MARGIN, 12 * mm, doc.title or "")
    canvas.drawRightString(A4[0] - PAGE_MARGIN, 12 * mm, f"Page {doc.page}")
    canvas.line(PAGE_MARGIN, 15 * mm, A4[0] - PAGE_MARGIN, 15 * mm)
    canvas.restoreState()


def render(src: Path, dest: Path) -> None:
    md = src.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", md, re.M)
    title = title_match.group(1).strip() if title_match else src.stem
    doc = SimpleDocTemplate(
        str(dest), pagesize=A4,
        leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
        topMargin=PAGE_MARGIN + 6 * mm, bottomMargin=22 * mm,
        title=title, author=BRAND, subject=title,
    )
    doc.build(parse(md, styles()), onFirstPage=decorate, onLaterPages=decorate)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dest = Path(argv[1]), Path(argv[2])
    if not src.exists():
        print(f"not found: {src}")
        return 1
    render(src, dest)
    print(f"wrote {dest} ({dest.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
