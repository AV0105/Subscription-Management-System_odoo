"""
Person 3 demo seed script.

This script seeds demo data needed to test and demonstrate:
- payment recording
- outstanding balance
- paid invoice status
- overdue invoices
- dashboard figures

Run from the subscription_app directory:

    python seed_person3_demo.py

Or from Flask shell:

    from seed_person3_demo import seed_person3_demo
    seed_person3_demo()

Demo logins created:
    admin@brightloop.test     / Demo@12345
    staff@brightloop.test     / Demo@12345
    customer@brightloop.test  / Demo@12345
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import func
from werkzeug.security import generate_password_hash


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)


try:
    from .extensions import db
    from .models import (
        User,
        Product,
        Plan,
        Subscription,
        SubscriptionLine,
        Invoice,
        Payment,
    )
except ImportError:
    from extensions import db
    from models import (
        User,
        Product,
        Plan,
        Subscription,
        SubscriptionLine,
        Invoice,
        Payment,
    )


TWO_PLACES = Decimal('0.01')


def _money(value):
    """
    Convert value to Decimal(10,2)-style money.
    """
    if value is None:
        return Decimal('0.00')

    try:
        return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def _get_or_create_user(email, name, role, password):
    """
    Create a user if the email does not already exist.
    Existing users are left unchanged.
    """
    user = User.query.filter_by(email=email).first()

    if user:
        return user, False

    user = User(
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
        role=role,
    )

    db.session.add(user)
    db.session.flush()

    return user, True


def _get_or_create_product(name, product_type, sales_price, cost_price, notes):
    """
    Create a product if it does not already exist.
    """
    product = Product.query.filter_by(name=name).first()

    if product:
        return product, False

    product = Product(
        name=name,
        product_type=product_type,
        sales_price=_money(sales_price),
        cost_price=_money(cost_price),
        notes=notes,
    )

    db.session.add(product)
    db.session.flush()

    return product, True


def _get_or_create_plan(name, billing_frequency, minimum_quantity, price):
    """
    Create a plan if it does not already exist.
    """
    today = date.today()

    plan = Plan.query.filter_by(name=name).first()

    if plan:
        return plan, False

    plan = Plan(
        name=name,
        billing_frequency=billing_frequency,
        minimum_quantity=minimum_quantity,
        price=_money(price),
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=365),
        auto_close=False,
        allow_renewal=True,
    )

    db.session.add(plan)
    db.session.flush()

    return plan, True


def _get_or_create_subscription(subscription_number, customer, plan, status):
    """
    Create a subscription if the subscription number does not already exist.
    """
    today = date.today()

    subscription = Subscription.query.filter_by(
        subscription_number=subscription_number
    ).first()

    if subscription:
        return subscription, False

    subscription = Subscription(
        subscription_number=subscription_number,
        customer_id=customer.id,
        plan_id=plan.id,
        start_date=today - timedelta(days=30),
        expiry_date=today + timedelta(days=30),
        status=status,
    )

    db.session.add(subscription)
    db.session.flush()

    return subscription, True


def _ensure_subscription_line(subscription, product, quantity, unit_price):
    """
    Add one subscription line if the subscription has no lines.
    """
    line_count = SubscriptionLine.query.filter_by(
        subscription_id=subscription.id
    ).count()

    if line_count > 0:
        return None, False

    unit_price = _money(unit_price)
    line_total = unit_price * quantity

    line = SubscriptionLine(
        subscription_id=subscription.id,
        product_id=product.id,
        quantity=quantity,
        unit_price=unit_price,
        line_total=line_total,
    )

    db.session.add(line)
    db.session.flush()

    return line, True


def _get_or_create_invoice(
    invoice_number,
    subscription,
    customer,
    issue_date,
    due_date,
    total,
    status,
):
    """
    Create an invoice if the invoice number does not already exist.
    """
    invoice = Invoice.query.filter_by(invoice_number=invoice_number).first()

    if invoice:
        return invoice, False

    total = _money(total)

    invoice = Invoice(
        invoice_number=invoice_number,
        subscription_id=subscription.id,
        customer_id=customer.id,
        issue_date=issue_date,
        due_date=due_date,
        subtotal=total,
        tax_amount=_money(0),
        discount_amount=_money(0),
        total=total,
        status=status,
    )

    db.session.add(invoice)
    db.session.flush()

    return invoice, True


def _add_payment_if_missing(invoice, amount, payment_method, payment_date, note):
    """
    Add a payment only if the same payment does not already exist.
    """
    amount = _money(amount)

    existing_payment = Payment.query.filter_by(
        invoice_id=invoice.id,
        amount=amount,
        payment_method=payment_method,
        payment_date=payment_date,
    ).first()

    if existing_payment:
        return existing_payment, False

    payment = Payment(
        invoice_id=invoice.id,
        amount=amount,
        payment_method=payment_method,
        payment_date=payment_date,
        note=note,
    )

    db.session.add(payment)
    db.session.flush()

    return payment, True


def _sync_invoice_status(invoice):
    """
    Update invoice status based on payments.

    Outstanding = invoice.total - sum(payments.amount)

    If outstanding is zero, invoice becomes Paid.
    Otherwise, keep it as Confirmed for MVP.
    """
    paid_amount = db.session.query(
        func.coalesce(func.sum(Payment.amount), 0)
    ).filter(
        Payment.invoice_id == invoice.id
    ).scalar()

    paid_amount = _money(paid_amount)
    invoice_total = _money(invoice.total)
    outstanding = invoice_total - paid_amount

    if outstanding <= Decimal('0.00'):
        invoice.status = 'Paid'
    elif invoice.status not in ('Draft', 'Cancelled'):
        invoice.status = 'Confirmed'


def seed_person3_demo():
    """
    Seed demo users, catalog data, subscription, invoices, and payments.
    """
    today = date.today()

    # Create tables if they do not exist.
    # In a real team setup, Person 1 may manage migrations/schema creation.
    db.create_all()

    try:
        # Users
        admin, admin_created = _get_or_create_user(
            email='admin@brightloop.test',
            name='Demo Admin',
            role='Admin',
            password='Demo@12345',
        )

        staff, staff_created = _get_or_create_user(
            email='staff@brightloop.test',
            name='Demo Staff',
            role='Staff',
            password='Demo@12345',
        )

        customer, customer_created = _get_or_create_user(
            email='customer@brightloop.test',
            name='Demo Customer',
            role='Customer',
            password='Demo@12345',
        )

        # Product
        product, product_created = _get_or_create_product(
            name='Brightloop Business Suite',
            product_type='Software',
            sales_price='150.00',
            cost_price='60.00',
            notes='Demo subscription product for Person 3 testing.',
        )

        # Plan
        plan, plan_created = _get_or_create_plan(
            name='Monthly Standard Plan',
            billing_frequency='monthly',
            minimum_quantity=1,
            price='150.00',
        )

        # Subscription
        subscription, subscription_created = _get_or_create_subscription(
            subscription_number='SUB-DEMO-001',
            customer=customer,
            plan=plan,
            status='Confirmed',
        )

        # Subscription line
        line, line_created = _ensure_subscription_line(
            subscription=subscription,
            product=product,
            quantity=2,
            unit_price='150.00',
        )

        # Invoice 1: fully paid
        invoice_paid, invoice_paid_created = _get_or_create_invoice(
            invoice_number='INV-DEMO-1001',
            subscription=subscription,
            customer=customer,
            issue_date=today - timedelta(days=20),
            due_date=today - timedelta(days=5),
            total='300.00',
            status='Confirmed',
        )

        # Invoice 2: partially paid, not overdue
        invoice_partial, invoice_partial_created = _get_or_create_invoice(
            invoice_number='INV-DEMO-1002',
            subscription=subscription,
            customer=customer,
            issue_date=today - timedelta(days=10),
            due_date=today + timedelta(days=7),
            total='300.00',
            status='Confirmed',
        )

        # Invoice 3: overdue and unpaid
        invoice_overdue, invoice_overdue_created = _get_or_create_invoice(
            invoice_number='INV-DEMO-1003',
            subscription=subscription,
            customer=customer,
            issue_date=today - timedelta(days=20),
            due_date=today - timedelta(days=5),
            total='300.00',
            status='Confirmed',
        )

        # Payments
        _add_payment_if_missing(
            invoice=invoice_paid,
            amount='300.00',
            payment_method='Bank Transfer',
            payment_date=today - timedelta(days=6),
            note='Full demo payment',
        )

        _add_payment_if_missing(
            invoice=invoice_partial,
            amount='150.00',
            payment_method='Card',
            payment_date=today - timedelta(days=1),
            note='Partial demo payment',
        )

        # Sync invoice statuses based on payments
        _sync_invoice_status(invoice_paid)
        _sync_invoice_status(invoice_partial)
        _sync_invoice_status(invoice_overdue)

        db.session.commit()

        print('Person 3 demo seed completed.')
        print('')

        if admin_created:
            print('Created admin user: admin@brightloop.test / Demo@12345')
        else:
            print('Admin user already existed: admin@brightloop.test')

        if staff_created:
            print('Created staff user: staff@brightloop.test / Demo@12345')
        else:
            print('Staff user already existed: staff@brightloop.test')

        if customer_created:
            print('Created customer user: customer@brightloop.test / Demo@12345')
        else:
            print('Customer user already existed: customer@brightloop.test')

        print('')

        if product_created:
            print('Created product: Brightloop Business Suite')
        else:
            print('Product already existed: Brightloop Business Suite')

        if plan_created:
            print('Created plan: Monthly Standard Plan')
        else:
            print('Plan already existed: Monthly Standard Plan')

        if subscription_created:
            print('Created subscription: SUB-DEMO-001')
        else:
            print('Subscription already existed: SUB-DEMO-001')

        if line_created:
            print('Created subscription line: 2 x Brightloop Business Suite')
        elif line is None:
            print('Subscription line already existed or was not needed.')

        print('')

        if invoice_paid_created:
            print('Created invoice: INV-DEMO-1001, fully paid')
        else:
            print('Invoice already existed: INV-DEMO-1001')

        if invoice_partial_created:
            print('Created invoice: INV-DEMO-1002, partially paid')
        else:
            print('Invoice already existed: INV-DEMO-1002')

        if invoice_overdue_created:
            print('Created invoice: INV-DEMO-1003, overdue unpaid')
        else:
            print('Invoice already existed: INV-DEMO-1003')

        print('')
        print('Expected demo results:')
        print('INV-DEMO-1001: Paid')
        print('INV-DEMO-1002: Outstanding 150.00')
        print('INV-DEMO-1003: Overdue, outstanding 300.00')

    except Exception:
        db.session.rollback()
        raise


def _get_app():
    """
    Try to import the Flask app from common project entry points.
    """
    try:
        from app import app
        return app
    except ImportError:
        pass

    try:
        from app import create_app
        return create_app()
    except ImportError:
        pass

    try:
        from subscription_app.app import app
        return app
    except ImportError:
        pass

    try:
        from subscription_app.app import create_app
        return create_app()
    except ImportError:
        pass

    raise RuntimeError(
        'Could not import the Flask app automatically. '
        'Run this seed function inside an app context instead.'
    )


if __name__ == '__main__':
    app = _get_app()

    with app.app_context():
        seed_person3_demo()