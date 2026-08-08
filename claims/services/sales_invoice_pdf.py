"""Render an ECABS sales invoice to a PDF matching the ECABS invoice layout.

Pure reportlab so it runs on any host. Two-column header (bill-to client on the
left, ECABS issuer on the right under the ECABS logo), a VAT-inclusive line
table, a totals box (Total Discount / Net / VAT / Total and the paid / pending
status) and a VAT amount specification footer.
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
DUE_BG = colors.HexColor("#e6f4ea")
PENDING_BG = colors.HexColor("#fdecea")


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
    """Locate the ECABS logo across the static dirs / collected root."""
    for base in list(settings.STATICFILES_DIRS) + [settings.STATIC_ROOT]:
        if not base:
            continue
        p = Path(base) / "img" / "companies" / "ecabs.png"
        if p.exists():
            return str(p)
    return None


def _logo_flowable():
    """The ECABS logo image, or a text badge if it can't be found."""
    path = _logo_path()
    if path:
        # Native 491x184; scale to a 42mm-wide badge preserving aspect ratio.
        w = 42 * mm
        img = Image(path, width=w, height=w * 184 / 491)
        img.hAlign = "RIGHT"
        return img
    return _p("eCabs", 18, BRAND, bold=True, align=2)


def _fields_table(rows, label_w, val_w):
    """A two-column label/value stack used for the header detail blocks."""
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


def build_sales_invoice_pdf(inv):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title="", author="", subject="", creator="",
    )
    story = []

    # --- Title band: "Invoice" + page, ECABS logo -----------------------------
    title_row = Table(
        [[_p("<b>Invoice</b>", 20, BRAND), [
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

    # --- Header: bill-to (client) | ECABS (issuer) ----------------------------
    bill_addr = (inv.bill_to_address or "").replace("\n", "<br/>")
    issuer_addr = (inv.issuer_address or "").replace("\n", "<br/>")

    left = [
        _p(inv.bill_to_name or "—", 11, INK, bold=True),
        Spacer(1, 1),
        _p(bill_addr, 8.5, INK),
        Spacer(1, 6),
        _fields_table([
            ("Bill-to Customer No.", inv.bill_customer_no or "—"),
            ("VAT Registration No.", inv.bill_vat_reg_no or "—"),
            ("Invoice No.", inv.invoice_no or "—"),
            ("Document Date", inv.document_date.strftime("%d %B %Y") if inv.document_date else "—"),
            ("Due Date", inv.due_date.strftime("%d %B %Y") if inv.due_date else "—"),
            ("Company Reg. No.", inv.bill_company_reg_no or "—"),
            ("Payment Terms", inv.payment_terms or "—"),
        ], 34 * mm, 50 * mm),
    ]
    right = [
        _p(inv.issuer_name or "eCabs", 11, INK, bold=True, align=0),
        Spacer(1, 1),
        _p(issuer_addr, 8.5, INK),
        Spacer(1, 6),
        _fields_table([
            ("Email", inv.issuer_email or "—"),
            ("Home Page", inv.issuer_website or "—"),
            ("Phone No.", inv.issuer_phone or "—"),
            ("VAT Registration No.", inv.issuer_vat or "—"),
            ("EXO Number", inv.issuer_exo or "—"),
        ], 32 * mm, 46 * mm),
    ]
    header = Table([[left, right]], colWidths=[92 * mm, 86 * mm])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [header, Spacer(1, 14)]

    # --- Line items -----------------------------------------------------------
    head = [
        _p("Amount", 8, colors.white, bold=True, align=1),
        _p("Description", 8, colors.white, bold=True),
        _p("Original Amount Incl. VAT", 8, colors.white, bold=True, align=2),
        _p("Discount Amount incl. VAT", 8, colors.white, bold=True, align=2),
        _p("Price with Discount incl. VAT", 8, colors.white, bold=True, align=2),
    ]
    rows = [head]
    for ln in inv.lines.all():
        qty = ln.quantity
        qty_txt = f"{qty:g}" if qty is not None else ""
        rows.append([
            _p(qty_txt, 8, align=1),
            _p(ln.description, 8),
            _p(_euro(ln.original_amount_incl_vat), 8, align=2),
            _p(_euro(ln.discount_amount_incl_vat), 8, align=2),
            _p(_euro(ln.price_with_discount), 8, align=2),
        ])
    while len(rows) < 8:
        rows.append(["", "", "", "", ""])

    tbl = Table(
        rows,
        colWidths=[16 * mm, 74 * mm, 30 * mm, 29 * mm, 29 * mm],
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

    # --- Totals ---------------------------------------------------------------
    def trow(label, value, bold=False):
        return _p(label, 8.5, INK, bold=bold, align=2), _p(f"€{value}", 9, INK, bold=bold, align=2)

    totals_data = [
        trow("Total Discount", _euro(inv.total_discount)),
        trow("Net", _euro(inv.net)),
        trow(f"VAT @ {inv.vat_rate:g}%", _euro(inv.vat_amount)),
        trow("Total", _euro(inv.total), bold=True),
    ]
    totals = Table(totals_data, colWidths=[44 * mm, 30 * mm])
    totals.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#dddddd")),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    if inv.is_paid:
        status_txt, status_bg = "The invoice has been paid.", DUE_BG
    else:
        status_txt, status_bg = f"Pending Invoice  €{_euro(inv.total)}", PENDING_BG
    status_box = Table([[_p(status_txt, 9.5, INK, bold=True, align=1)]], colWidths=[74 * mm])
    status_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), status_bg),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    remarks = [
        _p("Remarks:", 8, MUTED),
        Spacer(1, 2),
        _p((inv.remarks or "").replace("\n", "<br/>"), 8, INK),
    ]
    bottom = Table([[remarks, [totals, Spacer(1, 6), status_box]]],
                   colWidths=[100 * mm, 78 * mm])
    bottom.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [bottom, Spacer(1, 16)]

    # --- VAT amount specification ---------------------------------------------
    story += [_p("VAT Amount Specification", 9, INK, bold=True), Spacer(1, 3)]
    vat_head = [
        _p("VAT Identifier", 8, colors.white, bold=True),
        _p("VAT %", 8, colors.white, bold=True, align=2),
        _p("VAT Base", 8, colors.white, bold=True, align=2),
        _p("VAT Amount", 8, colors.white, bold=True, align=2),
    ]
    vat_rows = [vat_head, [
        _p(f"NA-SOVAT{inv.vat_rate:g}", 8),
        _p(f"{inv.vat_rate:g}", 8, align=2),
        _p(_euro(inv.net), 8, align=2),
        _p(_euro(inv.vat_amount), 8, align=2),
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
