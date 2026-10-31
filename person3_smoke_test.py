"""
Person 3 smoke test.

This script tests the Person 3 payment/dashboard flow automatically.

It uses the development server from:

    person3_dev_server.py

Run from the subscription_app directory:

    python person3_smoke_test.py

What it tests:
    - login through the dev login
    - dashboard page loads
    - dashboard cards are visible
    - overdue invoice appears
    - payment form shows open invoices
    - zero payment is rejected
    - negative payment is rejected
    - invalid payment amount is rejected
    - overpayment is rejected
    - full payment marks invoice as Paid
    - paid invoice disappears from payment form
    - partial payment keeps invoice open
    - partially paid overdue invoice remains overdue

This script creates temporary records prefixed with SMOKE.
It also deletes old SMOKE records at the start and end.
"""

import os
import sys
import uuid
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


try:
    from .person3_dev_server import create_dev_app
except ImportError:
    from person3_dev_server import create_dev_app


def _money(value):
    if value is None:
        return Decimal('0.00')

    try:
        return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')


def _get_paid_amount(invoice_id):
    paid = db.session.query(
        func.coalesce(func.sum(Payment.amount), 0)
    ).filter(
        Payment.invoice_id == invoice_id
    ).scalar()

    return _money(paid)


def _cleanup_smoke_data():
    """
    Delete temporary smoke-test records.

    Only records with obvious SMOKE prefixes are removed.
    """

    # Delete payments and invoices.
    smoke_invoice_ids = [
        invoice.id
        for invoice in Invoice.query.filter(
            Invoice.invoice_number.like('SMOKE-%')
        ).all()
    ]

    if smoke_invoice_ids:
        Payment.query.filter(
            Payment.invoice_id.in_(smoke_invoice_ids)
        ).delete(synchronize_session=False)

        Invoice.query.filter(
            Invoice.id.in_(smoke_invoice_ids)
        ).delete(synchronize_session=False)

    # Delete subscription lines and subscriptions.
    smoke_subscription_ids = [
        subscription.id
        for subscription in Subscription.query.filter(
            Subscription.subscription_number.like('SMOKE-SUB-%')
        ).all()
    ]

    if smoke_subscription_ids:
        SubscriptionLine.query.filter(
            SubscriptionLine.subscription_id.in_(smoke_subscription_ids)
        ).delete(synchronize_session=False)

        Subscription.query.filter(
            Subscription.id.in_(smoke_subscription_ids)
        ).delete(synchronize_session=False)

    # Delete smoke catalog records.
    Plan.query.filter(
        Plan.name.like('SMOKE PLAN %')
    ).delete(synchronize_session=False)

    Product.query.filter(
        Product.name.like('SMOKE PRODUCT %')
    ).delete(synchronize_session=False)

    # Delete smoke users.
    User.query.filter(
        User.email.like('smoke_%@brightloop.test')
    ).delete(synchronize_session=False)

    db.session.commit()


def _create_smoke_data(run_id):
    """
    Create temporary users, product, plan, subscription, and invoices.
    """

    today = date.today()

    admin_email = f'smoke_admin_{run_id}@brightloop.test'
    admin_password = 'Smoke@12345'

    customer_email = f'smoke_customer_{run_id}@brightloop.test'
    customer_password = 'Smoke@12345'

    admin = User(
        name='Smoke Admin',
        email=admin_email,
        password_hash=generate_password_hash(admin_password),
        role='Admin',
    )

    customer = User(
        name='Smoke Customer',
        email=customer_email,
        password_hash=generate_password_hash(customer_password),
        role='Customer',
    )

    product = Product(
        name=f'SMOKE PRODUCT {run_id}',
        product_type='Service',
        sales_price=_money('100.00'),
        cost_price=_money('10.00'),
        notes='Temporary smoke-test product.',
    )

    plan = Plan(
        name=f'SMOKE PLAN {run_id}',
        billing_frequency='monthly',
        minimum_quantity=1,
        price=_money('100.00'),
        start_date=today - timedelta(days=1),
        end_date=today + timedelta(days=365),
        auto_close=False,
        allow_renewal=True,
    )

    db.session.add_all([admin, customer, product, plan])
    db.session.flush()

    subscription = Subscription(
        subscription_number=f'SMOKE-SUB-{run_id}',
        customer_id=customer.id,
        plan_id=plan.id,
        start_date=today,
        expiry_date=today + timedelta(days=30),
        status='Confirmed',
    )

    db.session.add(subscription)
    db.session.flush()

    open_invoice = Invoice(
        invoice_number=f'SMOKE-OPEN-{run_id}',
        subscription_id=subscription.id,
        customer_id=customer.id,
        issue_date=today,
        due_date=today + timedelta(days=7),
        subtotal=_money('100.00'),
        tax_amount=_money('0.00'),
        discount_amount=_money('0.00'),
        total=_money('100.00'),
        status='Confirmed',
    )

    overdue_invoice = Invoice(
        invoice_number=f'SMOKE-OVERDUE-{run_id}',
        subscription_id=subscription.id,
        customer_id=customer.id,
        issue_date=today - timedelta(days=10),
        due_date=today - timedelta(days=1),
        subtotal=_money('50.00'),
        tax_amount=_money('0.00'),
        discount_amount=_money('0.00'),
        total=_money('50.00'),
        status='Confirmed',
    )

    db.session.add_all([open_invoice, overdue_invoice])
    db.session.commit()

    return {
        'admin_email': admin_email,
        'admin_password': admin_password,
        'open_invoice_id': open_invoice.id,
        'open_invoice_number': open_invoice.invoice_number,
        'overdue_invoice_id': overdue_invoice.id,
        'overdue_invoice_number': overdue_invoice.invoice_number,
    }


def run_smoke_tests():
    app = create_dev_app()
    app.config['TESTING'] = True

    with app.app_context():
        db.create_all()
        _cleanup_smoke_data()

    run_id = uuid.uuid4().hex[:8]

    with app.app_context():
        smoke = _create_smoke_data(run_id)

    try:
        with app.test_client() as client:

            print('Testing login...')

            response = client.post(
                '/login',
                data={
                    'email': smoke['admin_email'],
                    'password': smoke['admin_password'],
                },
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert b'Dashboard' in response.data

            print('Testing dashboard page and cards...')

            response = client.get('/dashboard')

            assert response.status_code == 200
            assert b'Total Invoiced' in response.data
            assert b'Payments Received' in response.data
            assert b'Outstanding Balance' in response.data
            assert b'Overdue Invoices' in response.data

            print('Testing overdue invoice appears on dashboard...')

            assert smoke['overdue_invoice_number'].encode() in response.data

            print('Testing payment form shows open invoices...')

            response = client.get('/payments/new')

            assert response.status_code == 200
            assert smoke['open_invoice_number'].encode() in response.data
            assert smoke['overdue_invoice_number'].encode() in response.data

            base_payment = {
                'payment_method': 'Card',
                'payment_date': date.today().isoformat(),
                'note': 'Smoke test payment',
            }

            print('Testing zero payment is rejected...')

            response = client.post(
                '/payments/new',
                data={
                    **base_payment,
                    'invoice_id': smoke['open_invoice_id'],
                    'amount': '0',
                },
                follow_redirects=True,
            )

            assert b'greater than zero' in response.data

            print('Testing negative payment is rejected...')

            response = client.post(
                '/payments/new',
                data={
                    **base_payment,
                    'invoice_id': smoke['open_invoice_id'],
                    'amount': '-10.00',
                },
                follow_redirects=True,
            )

            assert b'greater than zero' in response.data

            print('Testing invalid payment amount is rejected...')

            response = client.post(
                '/payments/new',
                data={
                    **base_payment,
                    'invoice_id': smoke['open_invoice_id'],
                    'amount': 'abc',
                },
                follow_redirects=True,
            )

            assert b'valid payment amount' in response.data

            print('Testing overpayment is rejected...')

            response = client.post(
                '/payments/new',
                data={
                    **base_payment,
                    'invoice_id': smoke['open_invoice_id'],
                    'amount': '99999.00',
                },
                follow_redirects=True,
            )

            assert b'cannot exceed' in response.data

            print('Confirming no payment was created after invalid attempts...')

            with app.app_context():
                assert _get_paid_amount(smoke['open_invoice_id']) == Decimal('0.00')

            print('Testing full payment marks invoice as Paid...')

            response = client.post(
                '/payments/new',
                data={
                    **base_payment,
                    'invoice_id': smoke['open_invoice_id'],
                    'amount': '100.00',
                },
                follow_redirects=True,
            )

            assert b'was recorded' in response.data
            assert smoke['open_invoice_number'].encode() in response.data

            with app.app_context():
                invoice = Invoice.query.get(smoke['open_invoice_id'])

                assert invoice is not None
                assert invoice.status == 'Paid'
                assert _get_paid_amount(invoice.id) == Decimal('100.00')

            print('Testing paid invoice disappears from payment form...')

            response = client.get('/payments/new')

            assert smoke['open_invoice_number'].encode() not in response.data

            print('Testing partial payment on overdue invoice...')

            response = client.post(
                '/payments/new',
                data={
                    **base_payment,
                    'invoice_id': smoke['overdue_invoice_id'],
                    'payment_method': 'Cash',
                    'amount': '20.00',
                },
                follow_redirects=True,
            )

            assert b'was recorded' in response.data

            with app.app_context():
                overdue_invoice = Invoice.query.get(smoke['overdue_invoice_id'])

                assert overdue_invoice is not None
                assert overdue_invoice.status == 'Confirmed'

                paid_amount = _get_paid_amount(overdue_invoice.id)
                outstanding = _money(overdue_invoice.total) - paid_amount

                assert paid_amount == Decimal('20.00')
                assert outstanding == Decimal('30.00')

            print('Testing partially paid overdue invoice remains overdue...')

            response = client.get('/dashboard')

            assert response.status_code == 200
            assert smoke['overdue_invoice_number'].encode() in response.data

        print('')
        print('ALL PERSON 3 SMOKE TESTS PASSED')

    finally:
        with app.app_context():
            _cleanup_smoke_data()


if __name__ == '__main__':
    try:
        run_smoke_tests()
    except AssertionError as error:
        print('')
        print('SMOKE TEST FAILED:')
        print(error)
        sys.exit(1)