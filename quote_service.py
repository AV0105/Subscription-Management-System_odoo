"""
quote_service.py
================
Business logic for Quotation management and Quote-to-Subscription conversion.

Person 2 owns this file.

Key public functions
--------------------
create_quotation(...)         → Quotation
quote_to_subscription(quo_id) → Subscription

Design
------
* A Quotation captures product lines at a quoted price (may differ from
  Product.sales_price — that is the point of quoting).
* quote_to_subscription() copies all line prices into SubscriptionLine
  as snapshots, so future price changes do not affect this subscription.
* The Quotation status becomes "Converted" after a successful conversion.
* An already-converted Quotation cannot be converted again.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import date

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Quotation, QuotationLine, QuotationStatus,
    Subscription, SubscriptionLine, SubscriptionStatus,
)
from invoice_service import _next_subscription_number, _to_decimal

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


class QuoteAlreadyConvertedError(Exception):
    """Raised when trying to convert an already-converted quotation."""


class QuoteNotAcceptedError(Exception):
    """Raised when trying to convert a quotation that is not Accepted."""


def _next_quotation_number() -> str:
    """Generate next QUO-XXXXX number."""
    from sqlalchemy import func
    result = db.session.execute(
        db.select(func.max(Quotation.quotation_number))
    ).scalar()
    if result:
        try:
            last_num = int(result.split("-")[1])
        except (IndexError, ValueError):
            last_num = 0
    else:
        last_num = 0
    return f"QUO-{last_num + 1:05d}"


def create_quotation(
    customer_id: int,
    plan_id: int,
    lines: list,           # list of dicts: {product_id, quantity, unit_price}
    valid_from: date = None,
    valid_until: date = None,
    notes: str = None,
) -> Quotation:
    """
    Create a new Quotation in Draft status.

    Args:
        customer_id:  User.id of the customer.
        plan_id:      Plan.id for this quote.
        lines:        List of {'product_id': int, 'quantity': int, 'unit_price': Decimal}.
        valid_from:   Optional start of validity window.
        valid_until:  Optional end of validity window.
        notes:        Optional note for the customer.

    Returns:
        Created Quotation object.
    """
    if not lines:
        raise ValueError("A quotation must have at least one line item.")

    try:
        quo = Quotation(
            quotation_number=_next_quotation_number(),
            customer_id=customer_id,
            plan_id=plan_id,
            valid_from=valid_from,
            valid_until=valid_until,
            notes=notes,
            status=QuotationStatus.DRAFT,
        )
        db.session.add(quo)
        db.session.flush()  # get quo.id

        for item in lines:
            qty = int(item["quantity"])
            price = _to_decimal(item["unit_price"])
            if qty <= 0:
                raise ValueError(f"Quantity must be > 0 (got {qty}).")
            if price < Decimal("0.00"):
                raise ValueError(f"Unit price cannot be negative (got {price}).")

            line = QuotationLine(
                quotation_id=quo.id,
                product_id=int(item["product_id"]),
                quantity=qty,
                unit_price=price,
            )
            line.calculate_total()
            db.session.add(line)

        db.session.commit()
        logger.info("Quotation %s created.", quo.quotation_number)
        return quo

    except Exception as exc:
        db.session.rollback()
        logger.error("Error creating quotation: %s", exc)
        raise


def quote_to_subscription(
    quotation_id: int,
    start_date: date = None,
    expiry_date: date = None,
    billing_day: int = None,
) -> Subscription:
    """
    Convert an Accepted Quotation into a Subscription.

    Copies all QuotationLine prices as SubscriptionLine unit_price snapshots.
    The Quotation status is set to Converted.

    Args:
        quotation_id:  Quotation.id to convert.
        start_date:    Subscription start date (defaults to today).
        expiry_date:   Optional expiry date.
        billing_day:   Optional billing day (1-28).

    Returns:
        The newly created Subscription (status = Draft, ready to Confirm).

    Raises:
        ValueError               — quotation not found or no lines.
        QuoteNotAcceptedError    — quotation must be in Accepted status.
        QuoteAlreadyConvertedError — quotation already converted.
    """
    quotation = db.session.get(Quotation, quotation_id)

    if quotation is None:
        raise ValueError(f"Quotation id={quotation_id} not found.")

    if quotation.status == QuotationStatus.CONVERTED:
        raise QuoteAlreadyConvertedError(
            f"Quotation {quotation.quotation_number} has already been converted to a subscription."
        )

    if quotation.status != QuotationStatus.ACCEPTED:
        raise QuoteNotAcceptedError(
            f"Quotation {quotation.quotation_number} must be Accepted before converting. "
            f"Current status: {quotation.status.value}."
        )

    if not quotation.lines:
        raise ValueError(
            f"Quotation {quotation.quotation_number} has no line items."
        )

    try:
        if start_date is None:
            start_date = date.today()

        sub = Subscription(
            subscription_number=_next_subscription_number(),
            customer_id=quotation.customer_id,
            plan_id=quotation.plan_id,
            start_date=start_date,
            expiry_date=expiry_date,
            billing_day=billing_day,
            quotation_id=quotation.id,
            status=SubscriptionStatus.DRAFT,
        )
        db.session.add(sub)
        db.session.flush()

        # Copy lines — prices locked from the quotation (snapshot)
        for q_line in quotation.lines:
            s_line = SubscriptionLine(
                subscription_id=sub.id,
                product_id=q_line.product_id,
                quantity=q_line.quantity,
                unit_price=_to_decimal(q_line.unit_price),  # snapshot from quote
            )
            s_line.calculate_total()
            db.session.add(s_line)

        # Mark quotation as converted
        quotation.status = QuotationStatus.CONVERTED

        db.session.commit()
        logger.info(
            "Quotation %s converted to Subscription %s.",
            quotation.quotation_number,
            sub.subscription_number,
        )
        return sub

    except (QuoteAlreadyConvertedError, QuoteNotAcceptedError, ValueError):
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        logger.error("Error converting quotation %s: %s", quotation_id, exc)
        raise RuntimeError(f"Could not convert quotation. No changes saved. Detail: {exc}") from exc

