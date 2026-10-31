from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from extensions import db
from models import Invoice, Payment, User


payment_bp = Blueprint('payments', __name__)

TWO_PLACES = Decimal('0.01')

# According to the document, invoice status is normally:
# Draft, Confirmed, Paid, Cancelled.
# We also allow Partially Paid in case the team chooses to use it.
ALLOWED_PAYMENT_STATUSES = ('Confirmed', 'Partially Paid')

PAYMENT_METHODS = (
    'Bank Transfer',
    'Card',
    'Cash',
    'Mobile Money',
    'Cheque',
    'Other',
)


def _to_money(value):
    """
    Convert a value to a Decimal with two decimal places.
    This is used for database values such as invoice.total.
    """
    if value is None:
        return Decimal('0.00')

    try:
        return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def _parse_money(value):
    """
    Parse user-entered amount.
    Raises InvalidOperation or ValueError if the amount is not valid.
    """
    raw = str(value or '').strip().replace(',', '')

    if not raw:
        raise InvalidOperation('Amount is required.')

    return Decimal(raw).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _paid_amount(invoice_id):
    """
    Calculate total paid amount for an invoice.
    Paid amount is the sum of all payments recorded against that invoice.
    """
    paid = db.session.query(
        func.coalesce(func.sum(Payment.amount), 0)
    ).filter(
        Payment.invoice_id == invoice_id
    ).scalar()

    return _to_money(paid)


def _outstanding_amount(invoice):
    """
    Outstanding balance = invoice.total - total payments received.
    """
    return _to_money(invoice.total) - _paid_amount(invoice.id)


def _parse_payment_date(value):
    """
    Parse payment date from HTML date input.
    Expected format: YYYY-MM-DD.
    If empty, use today's date.
    """
    raw = (value or '').strip()

    if not raw:
        return date.today()

    return datetime.strptime(raw, '%Y-%m-%d').date()


def require_finance_role(view):
    """
    Simple role guard.

    For MVP, this blocks obvious customer roles.
    If the role field is not implemented yet, it allows authenticated users
    so that integration with Person 1 does not break early.

    If your team uses exact roles, allowed roles can be tightened later.
    """
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        role = (getattr(current_user, 'role', '') or '').strip().lower()

        if role in {'customer', 'client', 'user'}:
            abort(403)

        return view(*args, **kwargs)

    return wrapped_view


@payment_bp.route('/payments')
@login_required
@require_finance_role
def payment_list():
    """
    Payment history page.
    Optionally filter by invoice_id.
    """
    invoice_id = request.args.get('invoice_id', type=int)

    records = (
        db.session.query(Payment, Invoice, User)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .join(User, Invoice.customer_id == User.id)
    )

    if invoice_id:
        records = records.filter(Payment.invoice_id == invoice_id)

    records = records.order_by(
        Payment.payment_date.desc(),
        Payment.id.desc()
    )

    payment_rows = []

    for payment, invoice, customer in records.all():
        payment_rows.append({
            'payment': payment,
            'invoice': invoice,
            'customer': customer,
        })

    selected_invoice = None
    selected_paid = Decimal('0.00')
    selected_outstanding = Decimal('0.00')

    if invoice_id:
        selected_invoice = Invoice.query.get(invoice_id)

        if selected_invoice:
            selected_paid = _paid_amount(selected_invoice.id)
            selected_outstanding = _outstanding_amount(selected_invoice)

    return render_template(
        'payments.html',
        payment_form=False,
        payment_rows=payment_rows,
        invoice_options=[],
        selected_invoice=selected_invoice,
        selected_paid=selected_paid,
        selected_outstanding=selected_outstanding,
        payment_methods=PAYMENT_METHODS,
        today=date.today().isoformat(),
    )


@payment_bp.route('/payments/new', methods=['GET', 'POST'])
@login_required
@require_finance_role
def payment_new():
    """
    Record a payment against an invoice.
    """
    today = date.today()
    selected_invoice_id = request.args.get('invoice_id', type=int)

    if request.method == 'POST':
        selected_invoice_id = request.form.get('invoice_id', type=int)

        if not selected_invoice_id:
            flash('Select an invoice before recording a payment.', 'danger')
            return redirect(url_for('payments.payment_new'))

        # Lock the invoice row to reduce race-condition risk.
        invoice = (
            Invoice.query
            .filter_by(id=selected_invoice_id)
            .with_for_update()
            .first()
        )

        if not invoice:
            flash('Invoice not found.', 'danger')
            return redirect(url_for('payments.payment_new'))
        
        current_status = invoice.status.value if hasattr(invoice.status, 'value') else str(invoice.status)
        if invoice.status not in ALLOWED_PAYMENT_STATUSES:
            flash('Payments can only be recorded for confirmed unpaid invoices.', 'danger')
            return redirect(url_for('payments.payment_new', invoice_id=invoice.id))

        try:
            amount = _parse_money(request.form.get('amount'))
        except (InvalidOperation, ValueError):
            flash('Enter a valid payment amount.', 'danger')
            return redirect(url_for('payments.payment_new', invoice_id=invoice.id))

        if amount <= Decimal('0.00'):
            flash('Payment amount must be greater than zero.', 'danger')
            return redirect(url_for('payments.payment_new', invoice_id=invoice.id))

        outstanding_before = _outstanding_amount(invoice)

        if outstanding_before <= Decimal('0.00'):
            flash('This invoice has no outstanding balance.', 'danger')
            return redirect(url_for('payments.payment_new', invoice_id=invoice.id))

        if amount > outstanding_before:
            flash(
                f'Payment cannot exceed the outstanding balance of {outstanding_before}.',
                'danger'
            )
            return redirect(url_for('payments.payment_new', invoice_id=invoice.id))

        try:
            payment_date = _parse_payment_date(request.form.get('payment_date'))
        except ValueError:
            flash('Enter a valid payment date in YYYY-MM-DD format.', 'danger')
            return redirect(url_for('payments.payment_new', invoice_id=invoice.id))

        payment_method = (request.form.get('payment_method') or '').strip()

        if payment_method not in PAYMENT_METHODS:
            flash('Select a valid payment method.', 'danger')
            return redirect(url_for('payments.payment_new', invoice_id=invoice.id))

        note = (request.form.get('note') or '').strip()

        payment = Payment(
            invoice_id=invoice.id,
            amount=amount,
            payment_method=payment_method,
            payment_date=payment_date,
            note=note,
        )

        try:
            db.session.add(payment)
            db.session.flush()

            outstanding_after = _outstanding_amount(invoice)

            if outstanding_after <= Decimal('0.00'):
                try:
                    from models import InvoiceStatus
                    invoice.status = InvoiceStatus.PAID
                except ImportError:
                    invoice.status = 'Paid'

            db.session.commit()

            flash(
                f'Payment of {amount} was recorded against invoice {invoice.invoice_number}.',
                'success'
            )

            return redirect(url_for('payments.payment_list', invoice_id=invoice.id))

        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to record payment')
            flash('The payment could not be recorded. Please try again.', 'danger')
            return redirect(url_for('payments.payment_new', invoice_id=invoice.id))

    # GET request: prepare payment form data.
    invoice_options = []

    invoices = (
        Invoice.query
        .filter(Invoice.status.in_(ALLOWED_PAYMENT_STATUSES))
        .order_by(Invoice.due_date.asc(), Invoice.id.desc())
        .all()
    )

    for invoice in invoices:
        paid_amount = _paid_amount(invoice.id)
        outstanding = _to_money(invoice.total) - paid_amount

        if outstanding > Decimal('0.00'):
            invoice_options.append({
                'invoice': invoice,
                'paid_amount': paid_amount,
                'outstanding': outstanding,
            })

    selected_invoice = None
    selected_paid = Decimal('0.00')
    selected_outstanding = Decimal('0.00')

    if selected_invoice_id:
        selected_invoice = Invoice.query.get(selected_invoice_id)

        if selected_invoice:
            selected_paid = _paid_amount(selected_invoice.id)
            selected_outstanding = _outstanding_amount(selected_invoice)

    return render_template(
        'payments.html',
        payment_form=True,
        payment_rows=[],
        invoice_options=invoice_options,
        selected_invoice=selected_invoice,
        selected_paid=selected_paid,
        selected_outstanding=selected_outstanding,
        payment_methods=PAYMENT_METHODS,
        today=today.isoformat(),
    )