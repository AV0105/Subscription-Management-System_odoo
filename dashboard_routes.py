from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required
from sqlalchemy import func

from extensions import db
from models import Invoice, Payment, Subscription


dashboard_bp = Blueprint('dashboard', __name__)

TWO_PLACES = Decimal('0.01')

# According to the document, the MVP subscription lifecycle is simple:
# Draft -> Confirmed/Active -> Closed
# We count both Active and Confirmed as active to stay safe during integration.
ACTIVE_SUBSCRIPTION_STATUSES = (
    'Active',
    'Confirmed',
    'active',
    'confirmed',
)

# Invoices that should be included in "Total Invoiced".
# Draft and Cancelled invoices are excluded.
INVOICED_STATUSES = (
    'Confirmed',
    'Partially Paid',
    'Paid',
    'confirmed',
    'partially paid',
    'paid',
)

# Open invoices that can still have an outstanding balance.
OPEN_INVOICE_STATUSES = (
    'Confirmed',
    'Partially Paid',
    'confirmed',
    'partially paid',
)


def _to_money(value):
    """
    Convert database money values to Decimal with two decimal places.
    """
    if value is None:
        return Decimal('0.00')

    try:
        return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def require_internal_role(view):
    """
    Simple role guard for the internal dashboard.

    For MVP, this blocks obvious customer roles.
    If the role field is not implemented yet, it allows authenticated users
    so that integration with Person 1 does not break early.
    """
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        role = (getattr(current_user, 'role', '') or '').strip().lower()

        if role in {'customer', 'client', 'user'}:
            abort(403)

        return view(*args, **kwargs)

    return wrapped_view


@dashboard_bp.route('/dashboard')
@login_required
@require_internal_role
def dashboard():
    """
    Dashboard page for finance/admin users.
    """
    today = date.today()

    # Active subscriptions count.
    active_subscriptions_count = (
        db.session.query(func.count(Subscription.id))
        .filter(Subscription.status.in_(ACTIVE_SUBSCRIPTION_STATUSES))
        .scalar() or 0
    )

    # Total invoiced amount.
    # This is not the same as payments received.
    total_invoiced = _to_money(
        db.session.query(func.coalesce(func.sum(Invoice.total), 0))
        .filter(Invoice.status.in_(INVOICED_STATUSES))
        .scalar()
    )

    # Total payments received.
    payments_received = _to_money(
        db.session.query(func.coalesce(func.sum(Payment.amount), 0)).scalar()
    )

    # Paid amount per invoice.
    paid_subquery = (
        db.session.query(
            Payment.invoice_id.label('invoice_id'),
            func.coalesce(func.sum(Payment.amount), 0).label('paid_amount')
        )
        .group_by(Payment.invoice_id)
        .subquery()
    )

    # Open invoices with paid amount.
    open_invoice_rows = (
        db.session.query(
            Invoice,
            func.coalesce(paid_subquery.c.paid_amount, 0).label('paid_amount')
        )
        .outerjoin(paid_subquery, Invoice.id == paid_subquery.c.invoice_id)
        .filter(Invoice.status.in_(OPEN_INVOICE_STATUSES))
        .all()
    )

    total_outstanding = Decimal('0.00')
    overdue_invoices = []

    for invoice, paid_amount in open_invoice_rows:
        invoice_total = _to_money(invoice.total)
        paid = _to_money(paid_amount)
        outstanding = invoice_total - paid

        # Protect against unexpected overpayment or inconsistent data.
        if outstanding < Decimal('0.00'):
            outstanding = Decimal('0.00')

        if outstanding > Decimal('0.00'):
            total_outstanding += outstanding

        # Overdue rule from the document:
        # due_date < today and outstanding > 0
        if invoice.due_date and invoice.due_date < today and outstanding > Decimal('0.00'):
            overdue_invoices.append({
                'invoice': invoice,
                'paid_amount': paid,
                'outstanding_amount': outstanding,
                'days_overdue': (today - invoice.due_date).days,
            })

    total_outstanding = total_outstanding.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    # Show oldest overdue invoices first.
    overdue_invoices.sort(key=lambda row: row['invoice'].due_date)

    return render_template(
        'dashboard.html',
        today=today,
        active_subscriptions_count=active_subscriptions_count,
        total_invoiced=total_invoiced,
        payments_received=payments_received,
        total_outstanding=total_outstanding,
        overdue_invoices=overdue_invoices,
        overdue_count=len(overdue_invoices),
    )