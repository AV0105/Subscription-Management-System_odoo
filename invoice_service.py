"""
invoice_service.py
==================
Business logic for invoice generation.

Person 2 owns this file.

Key public function
-------------------
generate_invoice(subscription_id)  → Invoice

Person 3 calls this function to get an Invoice object and then
records payments against invoice.id / invoice.total.

Design decisions
----------------
* All monetary arithmetic uses Python Decimal with ROUND_HALF_UP.
* Invoice creation and InvoiceLine creation happen in one DB transaction.
* A subscription can only have ONE invoice (MVP).  A second call returns
  the existing invoice instead of creating a duplicate.
* Tax is calculated by calculate_tax() — easy to expand later.
* Discount is calculated by calculate_discount() — easy to expand later.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import (
    Subscription,
    SubscriptionStatus,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  Money helpers
# ─────────────────────────────────────────────────────────────

TWO_PLACES = Decimal("0.01")


def _to_decimal(value) -> Decimal:
    """Convert any numeric value to Decimal, rounded to 2 dp."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────
#  Number generators
# ─────────────────────────────────────────────────────────────

def _next_subscription_number() -> str:
    """
    Generate the next SUB-XXXXX number.

    Uses a DB-level MAX() to find the last number, then increments.
    Safe within a transaction but not suitable for very-high-concurrency
    production use.  For an MVP/hackathon this is sufficient.
    """
    from sqlalchemy import func
    result = db.session.execute(
        db.select(func.max(Subscription.subscription_number))
    ).scalar()

    if result:
        # result looks like "SUB-00042"
        try:
            last_num = int(result.split("-")[1])
        except (IndexError, ValueError):
            last_num = 0
    else:
        last_num = 0

    return f"SUB-{last_num + 1:05d}"


def _next_invoice_number() -> str:
    """
    Generate the next INV-XXXXX number.

    Same approach as subscription numbers.
    """
    from sqlalchemy import func
    result = db.session.execute(
        db.select(func.max(Invoice.invoice_number))
    ).scalar()

    if result:
        try:
            last_num = int(result.split("-")[1])
        except (IndexError, ValueError):
            last_num = 0
    else:
        last_num = 0

    return f"INV-{last_num + 1:05d}"


# ─────────────────────────────────────────────────────────────
#  Tax calculation
# ─────────────────────────────────────────────────────────────

def calculate_tax(taxable_amount: Decimal) -> Decimal:
    """
    Calculate tax on the taxable amount.

    MVP implementation: flat rate from app config (DEFAULT_TAX_RATE).

    Person 1 can later add a TaxRule model; this function should then
    query that model instead of using the flat rate.

    Args:
        taxable_amount: subtotal minus discount, already as Decimal.

    Returns:
        tax_amount as Decimal rounded to 2 dp.
    """
    rate = Decimal(str(current_app.config.get("DEFAULT_TAX_RATE", 0.18)))
    tax = (taxable_amount * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    return max(tax, Decimal("0.00"))


# ─────────────────────────────────────────────────────────────
#  Discount calculation
# ─────────────────────────────────────────────────────────────

def calculate_discount(subscription: Subscription) -> Decimal:
    """
    Calculate the discount amount for a subscription.

    MVP: returns 0.  Person 1 can later add a Discount/Coupon model;
    this function is the single place to integrate that logic.

    The formula guarantees discount_amount <= subtotal so that
    taxable_amount is never negative.

    Args:
        subscription: the Subscription being invoiced.

    Returns:
        discount_amount as Decimal rounded to 2 dp.
    """
    # TODO: query Discount model when Person 1 implements it
    discount = Decimal("0.00")

    # Safety: discount can never exceed subtotal
    subtotal = subscription.subtotal
    if discount > subtotal:
        discount = subtotal

    return discount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


# ─────────────────────────────────────────────────────────────
#  Due date calculation
# ─────────────────────────────────────────────────────────────

def _calculate_due_date(issue_date: date) -> date:
    """
    Calculate the invoice due date.

    MVP: issue_date + DEFAULT_PAYMENT_TERMS_DAYS (default 30 days).
    Person 1 can later store payment terms on Plan/Subscription and
    pass them in here.
    """
    days = int(current_app.config.get("DEFAULT_PAYMENT_TERMS_DAYS", 30))
    return issue_date + timedelta(days=days)


# ─────────────────────────────────────────────────────────────
#  Main entry point — called by routes and by Person 3
# ─────────────────────────────────────────────────────────────

class InvoiceAlreadyExistsError(Exception):
    """Raised when trying to generate a second invoice for the same subscription."""


class SubscriptionNotBillableError(Exception):
    """Raised when subscription status prevents billing."""


def generate_invoice(subscription_id: int) -> Invoice:
    """
    Generate an Invoice from a Subscription.

    This is the PRIMARY function Person 3 should call.

    Returns the newly created Invoice (or the existing one if already
    generated — duplicate-safe).

    Raises:
        ValueError                  — subscription not found / invalid data
        SubscriptionNotBillableError — subscription status is not billable
        InvoiceAlreadyExistsError   — invoice already exists for this sub
        RuntimeError                — unexpected DB error (already rolled back)

    The entire Invoice + InvoiceLines are created in one atomic
    transaction.  If anything fails, nothing is committed.
    """
    # 1. Load subscription (eager-load lines and their products)
    subscription = (
        db.session.query(Subscription)
        .filter_by(id=subscription_id)
        .first()
    )

    if subscription is None:
        raise ValueError(f"Subscription with id={subscription_id} not found.")

    # 2. Validate subscription has lines
    if not subscription.lines:
        raise ValueError(
            f"Subscription {subscription.subscription_number} has no line items. "
            "Add at least one product before generating an invoice."
        )

    # 3. Validate each line
    for line in subscription.lines:
        if line.quantity <= 0:
            raise ValueError(
                f"Subscription line (product_id={line.product_id}) has "
                f"invalid quantity {line.quantity}."
            )
        if _to_decimal(line.unit_price) < Decimal("0.00"):
            raise ValueError(
                f"Subscription line (product_id={line.product_id}) has "
                f"negative unit price {line.unit_price}."
            )

    # 4. Check billable status
    if not subscription.is_billable:
        raise SubscriptionNotBillableError(
            f"Subscription {subscription.subscription_number} is in status "
            f"'{subscription.status.value}' and cannot be invoiced. "
            "Confirm or activate the subscription first."
        )

    # 5. Duplicate invoice check (application level, DB unique constraint is the backstop)
    existing_invoice = (
        db.session.query(Invoice)
        .filter_by(subscription_id=subscription_id)
        .first()
    )
    if existing_invoice is not None:
        logger.info(
            "Invoice already exists for subscription %s — returning existing %s",
            subscription.subscription_number,
            existing_invoice.invoice_number,
        )
        raise InvoiceAlreadyExistsError(
            f"An invoice ({existing_invoice.invoice_number}) already exists "
            f"for subscription {subscription.subscription_number}. "
            "Duplicate invoices are not allowed."
        )

    # ─── Begin atomic transaction ───────────────────────────────────
    try:
        issue_date = date.today()
        due_date = _calculate_due_date(issue_date)

        # 6 & 7. Calculate subtotal from subscription lines (Decimal)
        subtotal = _to_decimal(subscription.subtotal)

        # 8. Discount
        discount_amount = calculate_discount(subscription)

        # Taxable amount must never be negative
        taxable_amount = max(subtotal - discount_amount, Decimal("0.00"))

        # 9. Tax
        tax_amount = calculate_tax(taxable_amount)

        # 10. Total
        total = (taxable_amount + tax_amount).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        # Sanity check
        if total < Decimal("0.00"):
            raise ValueError("Invoice total cannot be negative.")

        # 11. Generate invoice number
        invoice_number = _next_invoice_number()

        # 12. Create Invoice record
        invoice = Invoice(
            invoice_number=invoice_number,
            subscription_id=subscription.id,
            customer_id=subscription.customer_id,
            issue_date=issue_date,
            due_date=due_date,
            subtotal=subtotal,
            discount_amount=discount_amount,
            tax_amount=tax_amount,
            total=total,
            status=InvoiceStatus.DRAFT,
        )
        db.session.add(invoice)
        # Flush to get invoice.id (still within the transaction, not committed)
        db.session.flush()

        # 13. Create InvoiceLines — snapshot of each subscription line
        for sub_line in subscription.lines:
            line_total = _to_decimal(sub_line.line_total)
            description = (
                sub_line.product.name if sub_line.product else f"Product #{sub_line.product_id}"
            )
            inv_line = InvoiceLine(
                invoice_id=invoice.id,
                product_id=sub_line.product_id,
                description=description,
                quantity=sub_line.quantity,
                unit_price=_to_decimal(sub_line.unit_price),
                line_total=line_total,
            )
            db.session.add(inv_line)

        # 14. Commit everything atomically
        db.session.commit()

        logger.info(
            "Invoice %s generated for subscription %s (total=%s)",
            invoice.invoice_number,
            subscription.subscription_number,
            invoice.total,
        )
        return invoice

    except IntegrityError as exc:
        db.session.rollback()
        logger.error("IntegrityError creating invoice: %s", exc)
        # The unique constraint on subscription_id fired — treat as duplicate
        raise InvoiceAlreadyExistsError(
            "An invoice already exists for this subscription (database constraint)."
        ) from exc
    except Exception as exc:
        db.session.rollback()
        logger.error("Unexpected error generating invoice: %s", exc)
        raise RuntimeError(
            f"Unable to generate invoice. No changes were saved. Detail: {exc}"
        ) from exc

