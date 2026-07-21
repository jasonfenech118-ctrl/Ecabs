"""
Sending claim correspondence by email.

Wraps Django's email framework so every message the app sends is also written
to EmailLog (outbound) against the claim, giving a single audit trail. In
development the console backend just prints the message; configure SMTP via the
environment variables in settings to send for real. Recovery documents
(statement / loss-of-use / repairs) can be attached as PDFs.
"""

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from claims.models import EmailLog

from .invoice_pdf import build_invoice_pdf, build_lou_pdf, build_repairs_pdf

# doc key -> (PDF builder, filename prefix, human label)
DOCUMENTS = {
    "statement": (build_invoice_pdf, "statement", "Statement"),
    "lossofuse": (build_lou_pdf, "loss-of-use", "Loss of earnings"),
    "repairs": (build_repairs_pdf, "repairs", "Repairs receipt"),
}


def document_pdf(claim, company, doc):
    """Return (filename, pdf_bytes) for a recovery document, or None if the
    document key is unknown."""
    entry = DOCUMENTS.get(doc)
    if not entry:
        return None
    builder, prefix, _ = entry
    ref = (claim.case_ref or claim.reference).replace(" ", "")
    return f"{prefix}-{ref}.pdf", builder(claim, company)


def send_claim_email(claim, *, to, subject, body, from_email=None,
                     user=None, attachments=None):
    """Send an email for a claim and record it in EmailLog.

    attachments: iterable of (filename, bytes, mimetype). Returns the EmailLog.
    Raises whatever the email backend raises on failure (nothing is logged
    unless the send succeeds).
    """
    from_email = from_email or settings.DEFAULT_FROM_EMAIL
    message = EmailMessage(subject=subject, body=body, from_email=from_email, to=[to])
    for name, content, mimetype in (attachments or []):
        message.attach(name, content, mimetype)
    message.send()  # raises on failure; we only log after a clean send
    return EmailLog.objects.create(
        claim=claim,
        direction=EmailLog.Direction.OUTBOUND,
        from_address=from_email,
        to_address=to,
        subject=subject,
        body=body,
        sent_at=timezone.now(),
        logged_by=user if getattr(user, "is_authenticated", False) else None,
    )


def default_chase_message(claim, company=None):
    """A ready-to-edit payment-chase subject + body for the third-party insurer."""
    ref = claim.case_ref or claim.reference
    greeting = claim.third_party_insurer or "Sir/Madam"
    subject = f"Payment reminder — claim {claim.tp_claim_number or ref}"
    lines = [
        f"Dear {greeting},",
        "",
        f"We refer to the above claim (our ref {ref}).",
    ]
    outstanding = claim.outstanding_amount
    if outstanding and outstanding > 0:
        lines.append(f"An amount of €{outstanding:.2f} remains outstanding.")
    lines += [
        "Kindly arrange settlement at your earliest convenience.",
        "",
        "Awaiting your remittance,",
    ]
    if company:
        lines.append(company.name)
        if company.email:
            lines.append(company.email)
    return subject, "\n".join(lines)
