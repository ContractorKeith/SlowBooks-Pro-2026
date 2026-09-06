"""Donor documents — acknowledgment letters (and, later, year-end giving
statements). A gift is a donation receipt (kind=invoice), a payment that
is not a receipt's own payment (kind=payment: a pledge payment or an
unapplied gift), or property (kind=in-kind)."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contacts import Customer
from app.services.donor_documents import gift_irs, load_gift, render_acknowledgment
from app.services.email_service import send_email
from app.services.pdf_service import generate_acknowledgment_letter_pdf
from app.services.settings_service import get_all_settings

router = APIRouter(prefix="/api/donors", tags=["donors"])

GIFT_KINDS = ("invoice", "payment", "in-kind")


class AcknowledgmentEmail(BaseModel):
    recipient: str | None = None
    subject: str | None = None


def _gift_or_404(db: Session, kind: str, gift_id: int):
    if kind not in GIFT_KINDS:
        raise HTTPException(status_code=404, detail="Unknown gift kind")
    try:
        gift = load_gift(db, kind, gift_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    customer = db.get(Customer, gift["customer_id"])
    if customer is None:
        raise HTTPException(status_code=404, detail="Donor not found")
    return gift, customer


def _letter(db: Session, kind: str, gift_id: int):
    gift, customer = _gift_or_404(db, kind, gift_id)
    company = get_all_settings(db)
    subject, body = render_acknowledgment(db, company, customer, gift)
    pdf = generate_acknowledgment_letter_pdf(
        customer, gift, gift_irs(company, gift), company, body, date.today()
    )
    return gift, customer, subject, body, pdf


@router.get("/gifts/{kind}/{gift_id}/acknowledgment/preview")
def acknowledgment_preview(kind: str, gift_id: int, db: Session = Depends(get_db)):
    """Is this a gift that gets a letter, and for how much? (A receipt's own
    payment is not — the receipt is acknowledged instead.)"""
    if kind not in GIFT_KINDS:
        raise HTTPException(status_code=404, detail="Unknown gift kind")
    try:
        gift = load_gift(db, kind, gift_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        return {"eligible": False, "amount": None, "reason": str(exc)}
    return {
        "eligible": True,
        "amount": float(gift["amount"]) if gift["amount"] is not None else None,
        "reason": None,
    }


@router.get("/gifts/{kind}/{gift_id}/acknowledgment/pdf")
def acknowledgment_pdf(kind: str, gift_id: int, db: Session = Depends(get_db)):
    gift, customer, _subject, _body, pdf = _letter(db, kind, gift_id)
    number = str(gift["number"] or gift_id).replace("/", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=Acknowledgment_{number}.pdf"
        },
    )


@router.post("/gifts/{kind}/{gift_id}/acknowledgment/email")
def acknowledgment_email(
    kind: str, gift_id: int, data: AcknowledgmentEmail, db: Session = Depends(get_db)
):
    gift, customer, subject, body, pdf = _letter(db, kind, gift_id)
    recipient = (data.recipient or customer.email or "").strip()
    if not recipient:
        raise HTTPException(status_code=400, detail="The donor has no email address")
    number = str(gift["number"] or gift_id).replace("/", "-")
    # send_email writes its own EmailLog row on every path.
    sent = send_email(
        db=db,
        to_email=recipient,
        subject=data.subject or subject,
        html_body=body,
        attachment_bytes=pdf,
        attachment_name=f"Acknowledgment_{number}.pdf",
        entity_type=f"acknowledgment_{kind}",
        entity_id=gift_id,
    )
    if not sent:
        raise HTTPException(
            status_code=502, detail="Email could not be sent (check SMTP settings)"
        )
    return {"sent": True, "recipient": recipient}
