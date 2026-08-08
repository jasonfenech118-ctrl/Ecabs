"""Render an eCabs parts receipt to a PDF, in the ECABS invoice layout.

Pure reportlab. Same visual family as the sales invoice (eCabs logo, header
blocks, line table, totals and a VAT amount specification), but the line table
carries part codes and VAT-exclusive prices with VAT added at the bottom.
"""

from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#666666")
BRAND = colors.HexColor("#111827")
HEAD_BG = colors.HexColor("#1f2937")
ROW_ALT = colors.HexColor("#f4f4f4")
PAID_BG = colors.HexColor("#e6f4ea")
DUE_BG = colors.HexColor("#fdecea")


class _CleanCanvas(Canvas):
    """Blank all identifying PDF metadata."""

    def save(self):
        for setter in ("setTitle", "setAuthor", "setSubject", "setCreator",
                       "setProducer", "setKeywords"):
            fn = getattr(self, setter, None)
            if fn:
                try:
                    fn("")
                except Exception:
                    pass
        super().save()


def _euro(v):
    return f"{Decimal(v or 0):,.2f}"


def _p(text, size=9, color=INK, bold=False, align=0, leading=None):
    style = ParagraphStyle(
        "c", fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size, textColor=color, leading=leading or size + 2, alignment=align,
    )
    return Paragraph(str(text if text is not None else ""), style)


def _logo_path():
    for base in list(settings.STATICFILES_DIRS) + [settings.STATIC_ROOT]:
        if not base:
            continue
        p = Path(base) / "img" / "companies" / "ecabs.png"
        if p.exists():
            return str(p)
    return None


def _logo_flowable():
    path = _logo_path()
    if path:
        w = 42 * mm
        img = Image(path, width=w, height=w * 184 / 491)
        img.hAlign = "RIGHT"
        return img
    return _p("eCabs", 18, BRAND, bold=True, align=2)


def _fields_table(rows, label_w, val_w):
    data = [[_p(lab, 8, MUTED), _p(val, 8.5, INK, bold=True)] for lab, val in rows]
    t = Table(data, colWidths=[label_w, val_w])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    return t


def build_parts_receipt_pdf(rec):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title="", author="", subject="", creator="",
    )
    story = []

    # --- Title band: "Parts Receipt" + page, eCabs logo ----------------------
    title_row = Table(
        [[_p("<b>Parts Receipt</b>", 20, BRAND), [
            _logo_flowable(),
            Spacer(1, 2),
            _p("Page 1 / 1", 8, MUTED, align=2),
        ]]],
        colWidths=[100 * mm, 78 * mm],
    )
    title_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (0, 0), "BOTTOM"),
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [title_row, Spacer(1, 12)]

    # --- Header: receipt details | eCabs (issuer) ----------------------------
    issuer_addr = (rec.issuer_address or "").replace("\n", "<br/>")
    claim_ref = rec.claim.reference if rec.claim_id else "—"

    left = [
        _p("Receipt details", 11, INK, bold=True),
        Spacer(1, 4),
        _fields_table([
            ("Receipt No.", rec.receipt_no or "—"),
            ("Receipt Date", rec.receipt_date.strftime("%d %B %Y") if rec.receipt_date else "—"),
            ("For claim", claim_ref),
            ("Vehicle reg", rec.vehicle_reg or "—"),
            ("Supplier", rec.supplier or "—"),
            ("Reference", rec.reference or "—"),
        ], 30 * mm, 54 * mm),
    ]
    right = [
        _p(rec.issuer_name or "eCabs", 11, INK, bold=True),
        Spacer(1, 1),
        _p(issuer_addr, 8.5, INK),
        Spacer(1, 6),
        _fields_table([
            ("Email", rec.issuer_email or "—"),
            ("Home Page", rec.issuer_website or "—"),
            ("Phone No.", rec.issuer_phone or "—"),
            ("VAT Registration No.", rec.issuer_vat or "—"),
            ("EXO Number", rec.issuer_exo or "—"),
        ], 32 * mm, 46 * mm),
    ]
    header = Table([[left, right]], colWidths=[92 * mm, 86 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [header, Spacer(1, 14)]

    # --- Line items: Qty | Part code | Description | Unit price | Total -------
    head = [
        _p("Qty", 8, colors.white, bold=True, align=1),
        _p("Part code", 8, colors.white, bold=True),
        _p("Description", 8, colors.white, bold=True),
        _p("Unit price", 8, colors.white, bold=True, align=2),
        _p("Total", 8, colors.white, bold=True, align=2),
    ]
    rows = [head]
    for ln in rec.lines.all():
        qty = ln.quantity
        qty_txt = f"{qty:g}" if qty is not None else ""
        rows.append([
            _p(qty_txt, 8, align=1),
            _p(ln.part_code, 8),
            _p(ln.description, 8),
            _p(_euro(ln.unit_price), 8, align=2),
            _p(_euro(ln.line_total), 8, align=2),
        ])
    while len(rows) < 8:
        rows.append(["", "", "", "", ""])

    tbl = Table(
        rows,
        colWidths=[14 * mm, 34 * mm, 68 * mm, 30 * mm, 32 * mm],
        repeatRows=1,
    )
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in range(1, len(rows)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
    tbl.setStyle(TableStyle(style))
    story += [tbl, Spacer(1, 12)]

    # --- Totals (VAT added at the bottom) ------------------------------------
    def trow(label, value, bold=False):
        return _p(label, 8.5, INK, bold=bold, align=2), _p(f"€{value}", 9, INK, bold=bold, align=2)

    totals_data = [
        trow("Subtotal", _euro(rec.subtotal)),
        trow(f"VAT @ {rec.vat_rate:g}%", _euro(rec.vat_amount)),
        trow("Total", _euro(rec.total), bold=True),
    ]
    totals = Table(totals_data, colWidths=[44 * mm, 30 * mm])
    totals.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#dddddd")),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    if rec.is_paid:
        status_txt, status_bg = "Paid.", PAID_BG
    else:
        status_txt, status_bg = f"Amount due  €{_euro(rec.total)}", DUE_BG
    status_box = Table([[_p(status_txt, 9.5, INK, bold=True, align=1)]], colWidths=[74 * mm])
    status_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), status_bg),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    notes = [
        _p("Notes:", 8, MUTED),
        Spacer(1, 2),
        _p((rec.notes or "").replace("\n", "<br/>"), 8, INK),
    ]
    bottom = Table([[notes, [totals, Spacer(1, 6), status_box]]],
                   colWidths=[100 * mm, 78 * mm])
    bottom.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [bottom, Spacer(1, 16)]

    # --- VAT amount specification --------------------------------------------
    story += [_p("VAT Amount Specification", 9, INK, bold=True), Spacer(1, 3)]
    vat_head = [
        _p("VAT Identifier", 8, colors.white, bold=True),
        _p("VAT %", 8, colors.white, bold=True, align=2),
        _p("VAT Base", 8, colors.white, bold=True, align=2),
        _p("VAT Amount", 8, colors.white, bold=True, align=2),
    ]
    vat_rows = [vat_head, [
        _p(f"NA-SOVAT{rec.vat_rate:g}", 8),
        _p(f"{rec.vat_rate:g}", 8, align=2),
        _p(_euro(rec.subtotal), 8, align=2),
        _p(_euro(rec.vat_amount), 8, align=2),
    ]]
    vat_tbl = Table(vat_rows, colWidths=[40 * mm, 20 * mm, 30 * mm, 30 * mm])
    vat_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story += [vat_tbl]

    doc.build(story, canvasmaker=_CleanCanvas)
    return buf.getvalue()
