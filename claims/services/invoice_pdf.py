"""Render a claim's statement/invoice to a PDF under a chosen company letterhead.

Uses reportlab (pure Python — works on any host, no system libraries), so the
invoice can open straight in the browser's PDF viewer.
"""

from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen.canvas import Canvas


class _CleanCanvas(Canvas):
    """Canvas that blanks all identifying PDF metadata (title/author/producer…)
    so the file properties reveal nothing."""

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


INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#666666")
HEAD_BG = colors.HexColor("#1a1a1a")
ROW_BG = colors.HexColor("#ececec")
NET_BG = colors.HexColor("#cfcfcf")


def _logo_path(company):
    if company.logo:
        return company.logo.path
    if company.logo_static:
        for base in list(settings.STATICFILES_DIRS) + [settings.STATIC_ROOT]:
            p = Path(base) / company.logo_static
            if p.exists():
                return str(p)
    return None


def _euro(v):
    return f"€ {Decimal(v or 0):,.2f}"


def build_invoice_pdf(claim, company):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="", author="", subject="", creator="",
    )
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=styles["Normal"], fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
    muted = ParagraphStyle("m", parent=normal, textColor=MUTED)
    bold = ParagraphStyle("b", parent=normal, fontName="Helvetica-Bold")
    right = ParagraphStyle("r", parent=normal, alignment=2)
    title = ParagraphStyle("t", parent=normal, fontName="Helvetica-Bold", fontSize=26, leading=30, alignment=2, spaceAfter=6)
    story = []
    stmt_no = claim.case_ref or claim.reference

    # --- Header: logo | STATEMENT + number ---
    logo_flowable = ""
    path = _logo_path(company)
    if path:
        img = Image(path)
        ratio = img.imageHeight / float(img.imageWidth)
        img.drawWidth = 55 * mm
        img.drawHeight = 55 * mm * ratio
        if img.drawHeight > 26 * mm:
            img.drawHeight = 26 * mm
            img.drawWidth = 26 * mm / ratio
        logo_flowable = img
    else:
        logo_flowable = Paragraph(company.name, bold)
    header = Table(
        [[logo_flowable,
          [Paragraph("STATEMENT", title),
           Paragraph(f"Statement No #: <b>{stmt_no}</b>", right)]]],
        colWidths=[90 * mm, 84 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 8 * mm)]

    # --- Company block | Date + Balance Due ---
    addr = company.address.replace("\n", "<br/>")
    from_block = [Paragraph(company.name, bold), Paragraph(addr, muted)]
    for val in [company.phone, company.email, company.website]:
        if val:
            from_block.append(Paragraph(val, normal))
    if company.vat_no:
        from_block.append(Paragraph(f"VAT No: {company.vat_no}", normal))
    from_block += [
        Spacer(1, 4 * mm),
        Paragraph("Bill To:", muted),
        Paragraph(claim.third_party_insurer or "—", bold),
    ]
    invoice_date = claim.bills_sent_on or timezone.localdate()
    summary = [
        Paragraph(f'Invoice date: <b>{invoice_date.strftime("%d/%m/%Y")}</b>', right),
        Spacer(1, 6 * mm),
        Paragraph(f"Balance Due: <b>{_euro(claim.total_claim_amount)}</b>", right),
    ]
    meta = Table([[from_block, summary]], colWidths=[110 * mm, 64 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [meta, Spacer(1, 8 * mm)]

    # --- Line items ---
    data = [["Item", "Quantity", "Rate", "Amount"]]
    for item, qty, rate, amount in claim.invoice_lines():
        data.append([item, str(qty), _euro(rate), _euro(amount)])
    if len(data) == 1:
        data.append(["No recovery amounts entered yet.", "", "", ""])
    total = claim.total_claim_amount
    data.append(["", "", "Subtotal:", _euro(total)])
    data.append(["", "", "Total NET:", _euro(total)])
    n = len(data)
    items = Table(data, colWidths=[86 * mm, 26 * mm, 31 * mm, 31 * mm])
    body_end = n - 3
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 1), (-1, body_end), ROW_BG),
        ("LINEBELOW", (0, 1), (-1, body_end), 2, colors.white),
        ("BACKGROUND", (0, n - 1), (-1, n - 1), NET_BG),
        ("FONTNAME", (2, n - 1), (-1, n - 1), "Helvetica-Bold"),
        ("FONTSIZE", (2, n - 1), (-1, n - 1), 11),
    ]
    items.setStyle(TableStyle(style))
    story += [items, Spacer(1, 8 * mm)]

    # --- Notes + bank details ---
    story.append(Paragraph("<b>Notes:</b>", normal))
    story.append(Paragraph(
        f'Claim No: <b>{claim.tp_claim_number or claim.insurer_claim_number}</b>', normal))
    story.append(Paragraph(f"Our Reg No: <b>{claim.vehicle_registration}</b>", normal))
    story.append(Paragraph(f"TP Reg No: <b>{claim.third_party_registration}</b>", normal))
    if company.bank_name:
        story.append(Spacer(1, 5 * mm))
        story.append(Paragraph(
            "Should you wish to send us the payment via direct debit our bank details are:", normal))
        story.append(Paragraph(f"<b>Bank Details – {company.bank_name}</b>", normal))
        if company.iban:
            story.append(Paragraph(f"IBAN – {company.iban}", normal))
        if company.account_no:
            story.append(Paragraph(f"Account No – {company.account_no}", normal))
        if company.swift:
            story.append(Paragraph(f"Swift Code – {company.swift}", normal))

    doc.build(story, canvasmaker=_CleanCanvas)
    return buf.getvalue()


def build_lou_pdf(claim, company):
    """Loss-of-use refund letter under a company letterhead — the second
    invoice type. Days x daily rate, net of running expenses."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="", author="", subject="", creator="",
    )
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=styles["Normal"], fontName="Helvetica", fontSize=10, leading=15, textColor=INK)
    bold = ParagraphStyle("b", parent=normal, fontName="Helvetica-Bold")
    right = ParagraphStyle("r", parent=normal, alignment=2)
    right_bold = ParagraphStyle("rb", parent=right, fontName="Helvetica-Bold")
    title_style = ParagraphStyle(
        "lt", parent=normal, fontName="Helvetica-Bold", fontSize=24,
        leading=28, alignment=2, spaceAfter=4,
    )
    story = []
    stmt_no = claim.case_ref or claim.reference

    # Header: logo left, company address right
    path = _logo_path(company)
    if path:
        img = Image(path)
        ratio = img.imageHeight / float(img.imageWidth)
        img.drawWidth = 50 * mm
        img.drawHeight = 50 * mm * ratio
        if img.drawHeight > 24 * mm:
            img.drawHeight = 24 * mm
            img.drawWidth = 24 * mm / ratio
        logo = img
    else:
        logo = Paragraph(company.name, bold)
    addr = company.address.replace("\n", "<br/>")
    header = Table(
        [[logo, Paragraph(f"<b>{company.name}</b><br/>{addr}", right)]],
        colWidths=[90 * mm, 80 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 10 * mm)]

    story.append(Paragraph("Loss of Earnings", title_style))
    story.append(Paragraph(f"Statement No: # <b>{stmt_no}</b>", right_bold))
    story.append(Spacer(1, 8 * mm))

    days = claim.loe_days or 0
    rate = claim.loe_daily_rate or Decimal("0")
    total = claim.loss_of_earnings
    details = [
        ("Claim No:", claim.tp_claim_number or claim.insurer_claim_number),
        ("Our Reg no:", claim.vehicle_registration),
        ("TP Reg no:", claim.third_party_registration),
        ("Make:", claim.vehicle_make_model),
        ("Loss of Use:", f"{days} DAYS"),
    ]
    rows = [[Paragraph(f"<b>{lbl}</b>", normal), Paragraph(str(val or ""), bold)]
            for lbl, val in details]
    dt = Table(rows, colWidths=[32 * mm, 120 * mm])
    dt.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    story += [dt, Spacer(1, 10 * mm)]

    story.append(Paragraph(
        f"Further to the above accident and mentioned details, kindly issue a "
        f"refund for {days} days loss of use at {_euro(rate)} daily (exc. VAT)",
        normal))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        f"Total amount due Net of Vat &nbsp;&nbsp;&nbsp; <b><u>{_euro(total)}</u></b>",
        normal))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "*Kindly note that the amount quoted is net of 30% running expenses.", normal))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "Should you require any additional information due not hesitate to contact us on:",
        normal))
    contact = company.email or company.phone
    if contact:
        story.append(Paragraph(contact, normal))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Awaiting your remittance,", normal))

    doc.build(story, canvasmaker=_CleanCanvas)
    return buf.getvalue()
