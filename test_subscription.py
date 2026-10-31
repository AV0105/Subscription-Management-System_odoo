"""
test_subscription.py
====================
Tests for Person 2's Subscription & Invoice module.

Run with:
    python -m pytest test_subscription.py -v

Uses SQLite in-memory via TestingConfig — no MySQL instance required.
"""

import pytest
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta

from app import create_app
from extensions import db as _db
from models import (
    User, Product, Plan,
    Subscription, SubscriptionLine, SubscriptionStatus,
    Invoice, InvoiceLine, InvoiceStatus, Role,
)
from invoice_service import (
    generate_invoice,
    InvoiceAlreadyExistsError,
    SubscriptionNotBillableError,
    _next_subscription_number,
    _next_invoice_number,
    calculate_tax,
    calculate_discount,
)


# ─────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Create application with testing config (SQLite in-memory)."""
    flask_app = create_app("testing")
    return flask_app


@pytest.fixture(scope="function")
def db(app):
    """
    Fresh database for every test.
    Creates all tables, runs the test, drops all tables.
    """
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def customer(db, app):
    """A sample customer user."""
    with app.app_context():
        user = User(
            name="Test Customer",
            email="customer@test.com",
            password_hash="hashed",
            role=Role.CUSTOMER,
        )
        db.session.add(user)
        db.session.commit()
        return db.session.get(User, user.id)


@pytest.fixture
def plan(db, app):
    """A sample plan."""
    with app.app_context():
        p = Plan(
            name="Test Monthly Plan",
            billing_frequency="Monthly",
            price=Decimal("99.00"),
        )
        db.session.add(p)
        db.session.commit()
        return db.session.get(Plan, p.id)


@pytest.fixture
def product(db, app):
    """A sample product."""
    with app.app_context():
        prod = Product(
            name="Software License",
            product_type="Service",
            sales_price=Decimal("100.00"),
        )
        db.session.add(prod)
        db.session.commit()
        return db.session.get(Product, prod.id)


@pytest.fixture
def product2(db, app):
    """A second product for multi-line tests."""
    with app.app_context():
        prod = Product(
            name="Support Package",
            product_type="Service",
            sales_price=Decimal("50.00"),
        )
        db.session.add(prod)
        db.session.commit()
        return db.session.get(Product, prod.id)


def _make_subscription(db, customer, plan, product, qty=2, price="500.00",
                        status=SubscriptionStatus.ACTIVE):
    """Helper: create a subscription with one line, in the given status."""
    sub = Subscription(
        subscription_number=_next_subscription_number(),
        customer_id=customer.id,
        plan_id=plan.id,
        start_date=date.today(),
        expiry_date=date.today() + timedelta(days=30),
        status=status,
    )
    db.session.add(sub)
    db.session.flush()

    line = SubscriptionLine(
        subscription_id=sub.id,
        product_id=product.id,
        quantity=qty,
        unit_price=Decimal(price),
    )
    line.calculate_total()
    db.session.add(line)
    db.session.commit()
    return db.session.get(Subscription, sub.id)


# ─────────────────────────────────────────────────────────────
#  Test 1 — Create subscription
# ─────────────────────────────────────────────────────────────

def test_create_subscription(db, app, customer, plan, product):
    """Valid customer + plan + line item creates a subscription."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00")
        assert sub is not None
        assert sub.subscription_number.startswith("SUB-")
        assert sub.customer_id == customer.id
        assert sub.plan_id == plan.id
        assert len(sub.lines) == 1


# ─────────────────────────────────────────────────────────────
#  Test 2 — Invalid quantity rejected
# ─────────────────────────────────────────────────────────────

def test_invalid_quantity_rejected(db, app, customer, plan, product):
    """Quantity 0 or negative must not pass server-side validation."""
    with app.app_context():
        sub = Subscription(
            subscription_number=_next_subscription_number(),
            customer_id=customer.id,
            plan_id=plan.id,
            start_date=date.today(),
            status=SubscriptionStatus.ACTIVE,
        )
        db.session.add(sub)
        db.session.flush()

        line = SubscriptionLine(
            subscription_id=sub.id,
            product_id=product.id,
            quantity=0,          # invalid
            unit_price=Decimal("100.00"),
        )
        line.calculate_total()
        db.session.add(line)
        db.session.commit()

        # generate_invoice must reject quantity=0
        with pytest.raises((ValueError, RuntimeError)) as exc_info:
            generate_invoice(sub.id)
        assert "quantity" in str(exc_info.value).lower() or "invalid" in str(exc_info.value).lower()


# ─────────────────────────────────────────────────────────────
#  Test 3 — Line total calculation
# ─────────────────────────────────────────────────────────────

def test_line_total_calculation(db, app, customer, plan, product):
    """2 × 500.00 = 1000.00"""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=2, price="500.00")
        line = sub.lines[0]
        assert Decimal(str(line.line_total)) == Decimal("1000.00")


# ─────────────────────────────────────────────────────────────
#  Test 4 — Multiple lines subtotal
# ─────────────────────────────────────────────────────────────

def test_multiple_lines_subtotal(db, app, customer, plan, product, product2):
    """Line1=1000, Line2=500 → subtotal=1500."""
    with app.app_context():
        sub = Subscription(
            subscription_number=_next_subscription_number(),
            customer_id=customer.id,
            plan_id=plan.id,
            start_date=date.today(),
            status=SubscriptionStatus.ACTIVE,
        )
        db.session.add(sub)
        db.session.flush()

        line1 = SubscriptionLine(
            subscription_id=sub.id,
            product_id=product.id,
            quantity=2,
            unit_price=Decimal("500.00"),
        )
        line1.calculate_total()

        line2 = SubscriptionLine(
            subscription_id=sub.id,
            product_id=product2.id,
            quantity=1,
            unit_price=Decimal("500.00"),
        )
        line2.calculate_total()

        db.session.add_all([line1, line2])
        db.session.commit()

        sub = db.session.get(Subscription, sub.id)
        assert sub.subtotal == Decimal("1500.00")


# ─────────────────────────────────────────────────────────────
#  Test 5 — Invoice generation
# ─────────────────────────────────────────────────────────────

def test_invoice_generation(db, app, customer, plan, product):
    """A valid Active subscription produces exactly one invoice."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.ACTIVE)
        invoice = generate_invoice(sub.id)

        assert invoice is not None
        assert invoice.invoice_number.startswith("INV-")
        assert invoice.subscription_id == sub.id
        assert invoice.customer_id == customer.id
        assert invoice.status == InvoiceStatus.DRAFT


# ─────────────────────────────────────────────────────────────
#  Test 6 — Invoice total formula
# ─────────────────────────────────────────────────────────────

def test_invoice_total_formula(db, app, customer, plan, product):
    """
    subtotal=100, discount=0, taxable=100, tax=18% → total=118.00
    """
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.ACTIVE)
        invoice = generate_invoice(sub.id)

        assert Decimal(str(invoice.subtotal)) == Decimal("100.00")
        assert Decimal(str(invoice.discount_amount)) == Decimal("0.00")
        assert Decimal(str(invoice.tax_amount)) == Decimal("18.00")
        assert Decimal(str(invoice.total)) == Decimal("118.00")


# ─────────────────────────────────────────────────────────────
#  Test 7 — Duplicate invoice prevention
# ─────────────────────────────────────────────────────────────

def test_duplicate_invoice_prevention(db, app, customer, plan, product):
    """Calling generate_invoice twice raises InvoiceAlreadyExistsError."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.ACTIVE)
        generate_invoice(sub.id)

        with pytest.raises(InvoiceAlreadyExistsError):
            generate_invoice(sub.id)

        # Verify only one invoice in DB
        count = Invoice.query.filter_by(subscription_id=sub.id).count()
        assert count == 1


# ─────────────────────────────────────────────────────────────
#  Test 8 — Snapshot: product price change does NOT affect invoice
# ─────────────────────────────────────────────────────────────

def test_snapshot_price_unchanged_after_product_update(db, app, customer, plan, product):
    """After generating invoice, changing Product.sales_price leaves invoice unchanged."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.ACTIVE)
        invoice = generate_invoice(sub.id)
        original_total = Decimal(str(invoice.total))

        # Change the product price
        prod = db.session.get(Product, product.id)
        prod.sales_price = Decimal("999.00")
        db.session.commit()

        # Reload invoice — total must be unchanged
        inv = db.session.get(Invoice, invoice.id)
        assert Decimal(str(inv.total)) == original_total


# ─────────────────────────────────────────────────────────────
#  Test 9 — Transaction rollback on error
# ─────────────────────────────────────────────────────────────

def test_draft_subscription_not_billable(db, app, customer, plan, product):
    """Draft subscription raises SubscriptionNotBillableError — no invoice created."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.DRAFT)

        with pytest.raises(SubscriptionNotBillableError):
            generate_invoice(sub.id)

        count = Invoice.query.filter_by(subscription_id=sub.id).count()
        assert count == 0, "No invoice should be created for a Draft subscription"


# ─────────────────────────────────────────────────────────────
#  Test 10 — Invoice starts in Draft status
# ─────────────────────────────────────────────────────────────

def test_invoice_initial_status_is_draft(db, app, customer, plan, product):
    """New invoice must have status=Draft."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.CONFIRMED)
        invoice = generate_invoice(sub.id)
        assert invoice.status == InvoiceStatus.DRAFT


# ─────────────────────────────────────────────────────────────
#  Test 11 — Invoice line snapshot
# ─────────────────────────────────────────────────────────────

def test_invoice_lines_created(db, app, customer, plan, product):
    """Invoice lines must be created with correct snapshot values."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=3, price="200.00",
                                  status=SubscriptionStatus.ACTIVE)
        invoice = generate_invoice(sub.id)

        assert len(invoice.lines) == 1
        inv_line = invoice.lines[0]
        assert inv_line.quantity == 3
        assert Decimal(str(inv_line.unit_price)) == Decimal("200.00")
        assert Decimal(str(inv_line.line_total)) == Decimal("600.00")


# ─────────────────────────────────────────────────────────────
#  Test 12 — Due date set correctly
# ─────────────────────────────────────────────────────────────

def test_due_date_30_days_from_issue(db, app, customer, plan, product):
    """Due date should be issue_date + DEFAULT_PAYMENT_TERMS_DAYS (30)."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.ACTIVE)
        invoice = generate_invoice(sub.id)

        expected_due = invoice.issue_date + timedelta(days=30)
        assert invoice.due_date == expected_due


# ─────────────────────────────────────────────────────────────
#  Test 13 — Subscription number uniqueness
# ─────────────────────────────────────────────────────────────

def test_subscription_numbers_are_unique(db, app, customer, plan, product, product2):
    """Two subscriptions must get different SUB numbers."""
    with app.app_context():
        sub1 = _make_subscription(db, customer, plan, product, qty=1, price="100.00")
        sub2 = _make_subscription(db, customer, plan, product2, qty=1, price="50.00")
        assert sub1.subscription_number != sub2.subscription_number


# ─────────────────────────────────────────────────────────────
#  Test 14 — Closed subscription not billable
# ─────────────────────────────────────────────────────────────

def test_closed_subscription_not_billable(db, app, customer, plan, product):
    """Closed subscription raises SubscriptionNotBillableError."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.CLOSED)

        with pytest.raises(SubscriptionNotBillableError):
            generate_invoice(sub.id)

