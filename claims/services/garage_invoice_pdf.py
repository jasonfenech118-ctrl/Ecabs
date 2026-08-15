"""Render a garage invoice (ACR Garage → hire company) to a PDF.

Pure reportlab so it runs on any host. Matches the garage's invoice layout:
orange banner, issuer + bill-to blocks, a repair-line table and a totals box
with subtotal, discount, VAT and balance due.
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

ORANGE = colors.HexColor("#F26522")
INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#666666")
HEAD_BG = colors.HexColor("#F26522")
ROW_ALT = colors.HexColor("#f4f4f4")
DUE_BG = colors.HexColor("#f8d5cd")


class _CleanCanvas(Canvas):
    """Blank all identifying PDF metadata and paint the orange banners."""

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

    def showPage(self):
        # Orange band across the very top of every page.
        w, h = A4
        self.setFillColor(ORANGE)
        self.rect(0, h - 8 * mm, w, 8 * mm, stroke=0, fill=1)
        super().showPage()


def _euro(v):
    return f"{Decimal(v or 0):,.2f}"


def _p(text, size=9, color=INK, bold=False, align=0, leading=None):
    style = ParagraphStyle(
        "c", fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size, textColor=color, leading=leading or size + 2, alignment=align,
    )
    return Paragraph(str(text or ""), style)


def _logo_path():
    """Locate the ACR Garage logo across the static dirs / collected root."""
    for base in list(settings.STATICFILES_DIRS) + [settings.STATIC_ROOT]:
        if not base:
            continue
        p = Path(base) / "img" / "acr-garage-logo.jpg"
        if p.exists():
            return str(p)
    return None


def _logo_flowable():
    """The real ACR Garage logo image, or a text box if it can't be found."""
    path = _logo_path()
    if path:
        # Native 863x394; scale to a 40mm-wide badge preserving aspect ratio.
        w = 40 * mm
        img = Image(path, width=w, height=w * 394 / 863)
        img.hAlign = "LEFT"
        return img
    inner = Table(
        [[_p("ACR", 20, ORANGE, bold=True)], [_p("Garage", 12, INK, bold=True)]],
        colWidths=[38 * mm],
    )
    inner.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("BOX", (0, 0), (-1, -1), 1.2, INK),
        ("LINEBELOW", (0, 0), (0, 0), 2, ORANGE),
    ]))
    return inner


def build_garage_invoice_pdf(inv):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title="", author="", subject="", creator="",
    )
    story = []

    # --- Header: logo | issuer details | date/invoice no ---------------------
    issuer = [
        _p(inv.issuer_name, 12, INK, bold=True),
        Spacer(1, 2),
        _p(f"PAYABLE TO {inv.payable_to.upper()}", 8, MUTED),
        Spacer(1, 4),
        _p(f"Contact Details&nbsp;&nbsp;&nbsp;{inv.issuer_contact}", 8, INK),
        _p(f"VAT No&nbsp;&nbsp;&nbsp;{inv.issuer_vat}", 8, INK),
        _p(f"Email&nbsp;&nbsp;&nbsp;{inv.issuer_email}", 8, INK),
    ]
    meta = [
        _p("Date:", 8, MUTED, align=2),
        _p(inv.invoice_date.strftime("%d/%m/%Y") if inv.invoice_date else "—", 9, INK, bold=True, align=2),
        Spacer(1, 8),
        _p("Invoice No:", 8, MUTED, align=2),
        _p(inv.invoice_no or "—", 9, ORANGE, bold=True, align=2),
    ]
    # Fastdrop keeps the registration per repair line; everyone else shows one
    # registration up in the header.
    if not inv.is_fastdrop:
        meta += [
            Spacer(1, 8),
            _p("Registration No:", 8, MUTED, align=2),
            _p((inv.vehicle_reg or "—").upper(), 9, INK, bold=True, align=2),
        ]
    header = Table(
        [[_logo_flowable(), issuer, meta]],
        colWidths=[42 * mm, 78 * mm, 58 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [header, Spacer(1, 10)]

    # --- Bill to -------------------------------------------------------------
    addr = (inv.bill_address or "").replace("\n", "<br/>")
    bill = Table([[
        [
            _p("BILL TO", 8, MUTED, bold=True),
            Spacer(1, 3),
            _p("Contact Name", 8, MUTED), _p(inv.bill_contact_name, 9, INK),
            _p("Client Company Name", 8, MUTED), _p(inv.bill_company_name, 9, INK),
            _p("Address", 8, MUTED), _p(addr, 9, INK),
            _p("Email", 8, MUTED), _p(inv.bill_email, 9, INK),
            _p("Client VAT", 8, MUTED), _p(inv.bill_vat, 9, INK),
        ],
        [_p(inv.bill_to, 13, INK, bold=True)],
    ]], colWidths=[95 * mm, 83 * mm])
    bill.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [bill, Spacer(1, 10)]

    # --- Line items ----------------------------------------------------------
    # Same three columns either way. On a Fast drop invoice each line also
    # carries its registration and date, written underneath the description
    # rather than in columns of their own.
    head = [_p("Description", 8, colors.white, bold=True),
            _p("Unit Price", 8, colors.white, bold=True, align=2),
            _p("Total", 8, colors.white, bold=True, align=2)]
    rows = [head]
    for ln in inv.lines.all():
        description = ln.description or ""
        if inv.is_fastdrop:
            beneath = [bit for bit in (
                ln.reg_no,
                ln.line_date.strftime("%d/%m/%Y") if ln.line_date else "",
            ) if bit]
            if beneath:
                description += (
                    f'<br/><font size="7" color="#666666">'
                    f'{" &nbsp;·&nbsp; ".join(beneath)}</font>'
                )
        rows.append([
            _p(description, 8, leading=11),
            _p(_euro(ln.unit_price) if ln.unit_price is not None else "", 8, align=2),
            _p(_euro(ln.amount), 8, align=2),
        ])
    while len(rows) < 12:
        rows.append(["", "", ""])
    col_widths = [134 * mm, 22 * mm, 22 * mm]

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
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
    story += [tbl, Spacer(1, 10)]

    # --- Remarks + totals ----------------------------------------------------
    def trow(label, value, bold=False, bg=None):
        lab = _p(label, 8, INK, bold=bold, align=2)
        val = _p(value, 9, INK, bold=bold, align=2)
        return lab, val

    totals_data = [
        trow("SUBTOTAL", _euro(inv.subtotal)),
        trow("DISCOUNT", f"{inv.discount_pct:.2f}%"),
        trow("SUBTOTAL LESS DISCOUNT", _euro(inv.subtotal_less_discount)),
        trow("VAT RATE", f"{inv.vat_rate:.2f}%"),
        trow("TOTAL VAT", _euro(inv.vat_amount)),
    ]
    totals = Table(totals_data, colWidths=[42 * mm, 26 * mm])
    totals.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    due = Table([[_p("Balance Due", 10, INK, bold=True, align=2),
                  _p(f"€{_euro(inv.balance_due)}", 11, INK, bold=True, align=2)]],
                colWidths=[42 * mm, 26 * mm])
    due.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (1, 0), DUE_BG),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    remarks = [
        _p("Remarks / Payment Instructions:", 8, MUTED),
        Spacer(1, 2),
        _p(inv.remarks.replace("\n", "<br/>"), 8, INK),
    ]
    bottom = Table([[remarks, [totals, Spacer(1, 6), due]]],
                   colWidths=[100 * mm, 78 * mm])
    bottom.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [bottom]

    # --- Payment note: kindly settle to this account -------------------------
    if inv.issuer_iban:
        pay = Table([[[
            _p("Kindly settle this invoice by bank transfer to:", 8.5, INK, bold=True),
            Spacer(1, 3),
            _p(f"Account holder&nbsp;&nbsp;&nbsp;{inv.payable_to}", 8, INK),
            _p(f"IBAN&nbsp;&nbsp;&nbsp;<b>{inv.issuer_iban}</b>", 8, INK),
        ]]], colWidths=[178 * mm])
        pay.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fbe9e1")),
            ("BOX", (0, 0), (-1, -1), 0.6, ORANGE),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story += [Spacer(1, 12), pay]

    doc.build(story, canvasmaker=_CleanCanvas)
    return buf.getvalue()
