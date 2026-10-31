"""
price_request_service.py
========================
Business logic for Customer Price Requests.

Person 2 owns this file.

A customer asks for a better unit price on one of their SubscriptionLine items.
Staff can approve (with an optional counter-price) or reject the request.
On approval the subscription line is updated atomically.

Key functions
-------------
submit_price_request(sub_line_id, customer_id, requested_price, note)
  → PriceRequest

approve_price_request(price_request_id, approved_price, staff_note)
  → PriceRequest   (subscription line also updated)

reject_price_request(price_request_id, staff_note)
  → PriceRequest
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime

from extensions import db
from models import (
    PriceRequest, PriceRequestStatus,
    SubscriptionLine, SubscriptionStatus,
)
from invoice_service import _to_decimal

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


class PriceRequestError(Exception):
    """Base error for price request operations."""


def submit_price_request(
    subscription_line_id: int,
    customer_id: int,
    requested_price: Decimal,
    customer_note: str = None,
) -> PriceRequest:
    """
    Submit a price negotiation request.

    Rules:
    - Customer must own the subscription containing this line.
    - Subscription must be Active or Confirmed (not Draft or Closed).
    - requested_price must be >= 0 and LESS THAN the current price
      (the customer wants a better deal, not a price increase).
    - Only one PENDING request per line is allowed at a time.

    Returns the created PriceRequest.
    """
    line = db.session.get(SubscriptionLine, subscription_line_id)
    if line is None:
        raise PriceRequestError("Subscription line not found.")

    sub = line.subscription
    if sub.customer_id != customer_id:
        raise PriceRequestError("You can only request prices for your own subscriptions.")

    if sub.status not in (SubscriptionStatus.CONFIRMED, SubscriptionStatus.ACTIVE):
        raise PriceRequestError(
            f"Price requests can only be submitted for Active or Confirmed subscriptions "
            f"(current status: {sub.status.value})."
        )

    requested_price = _to_decimal(requested_price)
    current_price = _to_decimal(line.unit_price)

    if requested_price < Decimal("0.00"):
        raise PriceRequestError("Requested price cannot be negative.")

    if requested_price >= current_price:
        raise PriceRequestError(
            f"Requested price ({requested_price}) must be less than the "
            f"current price ({current_price})."
        )

    # Only one open (Pending) request per line
    existing = PriceRequest.query.filter_by(
        subscription_line_id=subscription_line_id,
        status=PriceRequestStatus.PENDING,
    ).first()
    if existing:
        raise PriceRequestError(
            "A price request is already pending for this line. "
            "Please wait for it to be resolved."
        )

    try:
        pr = PriceRequest(
            subscription_line_id=subscription_line_id,
            customer_id=customer_id,
            current_price=current_price,
            requested_price=requested_price,
            customer_note=(customer_note or "").strip() or None,
            status=PriceRequestStatus.PENDING,
        )
        db.session.add(pr)
        db.session.commit()
        logger.info(
            "Price request submitted: line=%s current=%s requested=%s",
            subscription_line_id, current_price, requested_price,
        )
        return pr
    except Exception as exc:
        db.session.rollback()
        logger.error("Error submitting price request: %s", exc)
        raise PriceRequestError(f"Could not submit request. Detail: {exc}") from exc


def approve_price_request(
    price_request_id: int,
    approved_price: Decimal,
    staff_note: str = None,
) -> PriceRequest:
    """
    Approve a price request.

    approved_price may be any non-negative value — staff can offer a
    counter-price (higher than the customer's request but lower than current).
    The subscription line's unit_price and line_total are updated atomically.

    Returns the updated PriceRequest.
    """
    pr = db.session.get(PriceRequest, price_request_id)
    if pr is None:
        raise PriceRequestError("Price request not found.")

    if pr.status != PriceRequestStatus.PENDING:
        raise PriceRequestError(
            f"Only Pending requests can be approved (current: {pr.status.value})."
        )

    approved_price = _to_decimal(approved_price)
    if approved_price < Decimal("0.00"):
        raise PriceRequestError("Approved price cannot be negative.")

    try:
        line = pr.subscription_line

        # Update the subscription line price (this is a business decision — snapshot changes)
        line.unit_price = approved_price
        line.calculate_total()

        # Update the request
        pr.approved_price = approved_price
        pr.staff_note = (staff_note or "").strip() or None
        pr.status = PriceRequestStatus.APPROVED
        pr.resolved_at = datetime.utcnow()

        db.session.commit()
        logger.info(
            "Price request %s approved: line=%s new_price=%s",
            price_request_id, line.id, approved_price,
        )
        return pr
    except PriceRequestError:
        db.session.rollback()
        raise
    except Exception as exc:
        db.session.rollback()
        logger.error("Error approving price request %s: %s", price_request_id, exc)
        raise PriceRequestError(f"Could not approve request. Detail: {exc}") from exc


def reject_price_request(
    price_request_id: int,
    staff_note: str = None,
) -> PriceRequest:
    """
    Reject a price request. The subscription line price is NOT changed.

    Returns the updated PriceRequest.
    """
    pr = db.session.get(PriceRequest, price_request_id)
    if pr is None:
        raise PriceRequestError("Price request not found.")

    if pr.status != PriceRequestStatus.PENDING:
        raise PriceRequestError(
            f"Only Pending requests can be rejected (current: {pr.status.value})."
        )

    try:
        pr.staff_note = (staff_note or "").strip() or None
        pr.status = PriceRequestStatus.REJECTED
        pr.resolved_at = datetime.utcnow()
        db.session.commit()
        logger.info("Price request %s rejected.", price_request_id)
        return pr
    except Exception as exc:
        db.session.rollback()
        logger.error("Error rejecting price request %s: %s", price_request_id, exc)
        raise PriceRequestError(f"Could not reject request. Detail: {exc}") from exc

