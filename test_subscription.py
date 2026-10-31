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
    Quotation, QuotationLine, QuotationStatus,
    PriceRequest, PriceRequestStatus,
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
from quote_service import (
    create_quotation,
    quote_to_subscription,
    QuoteAlreadyConvertedError,
    QuoteNotAcceptedError,
)
from price_request_service import (
    submit_price_request,
    approve_price_request,
    reject_price_request,
    PriceRequestError,
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


# ─────────────────────────────────────────────────────────────
#  Test 15 — Billing day calculation & invoice issue date
# ─────────────────────────────────────────────────────────────

def test_billing_day_next_billing_date(db, app, customer, plan, product):
    """Subscription respects billing_day of month."""
    with app.app_context():
        sub = Subscription(
            subscription_number=_next_subscription_number(),
            customer_id=customer.id,
            plan_id=plan.id,
            start_date=date.today(),
            billing_day=15,
            status=SubscriptionStatus.ACTIVE,
        )
        db.session.add(sub)
        db.session.commit()

        billing_date = sub.next_billing_date()
        assert billing_date.day == 15
        assert billing_date >= date.today()


def test_invoice_generation_uses_billing_day(db, app, customer, plan, product):
    """When billing_day is configured, invoice.issue_date uses the next billing day."""
    with app.app_context():
        sub = Subscription(
            subscription_number=_next_subscription_number(),
            customer_id=customer.id,
            plan_id=plan.id,
            start_date=date.today(),
            billing_day=20,
            status=SubscriptionStatus.ACTIVE,
        )
        db.session.add(sub)
        db.session.flush()

        line = SubscriptionLine(
            subscription_id=sub.id,
            product_id=product.id,
            quantity=1,
            unit_price=Decimal("100.00"),
        )
        line.calculate_total()
        db.session.add(line)
        db.session.commit()

        invoice = generate_invoice(sub.id)
        assert invoice.issue_date.day == 20


# ─────────────────────────────────────────────────────────────
#  Test 16 & 17 — Turning a quote into a subscription
# ─────────────────────────────────────────────────────────────

def test_create_and_convert_quote_to_subscription(db, app, customer, plan, product):
    """Accepted quotation converts into a subscription with snapshot pricing."""
    with app.app_context():
        # Create quote with a special discounted price (80 instead of product sales price 100)
        quote = create_quotation(
            customer_id=customer.id,
            plan_id=plan.id,
            lines=[{"product_id": product.id, "quantity": 3, "unit_price": Decimal("80.00")}],
            valid_from=date.today(),
            valid_until=date.today() + timedelta(days=14),
            notes="Exclusive corporate discount",
        )
        assert quote.quotation_number.startswith("QUO-")
        assert quote.status == QuotationStatus.DRAFT
        assert quote.subtotal == Decimal("240.00")

        # Mark as accepted
        quote.status = QuotationStatus.ACCEPTED
        db.session.commit()

        # Convert to subscription
        sub = quote_to_subscription(
            quotation_id=quote.id,
            billing_day=1,
        )
        assert sub is not None
        assert sub.subscription_number.startswith("SUB-")
        assert sub.quotation_id == quote.id
        assert sub.billing_day == 1
        assert len(sub.lines) == 1

        # Check line snapshot: 3 * 80 = 240
        sub_line = sub.lines[0]
        assert sub_line.quantity == 3
        assert Decimal(str(sub_line.unit_price)) == Decimal("80.00")
        assert Decimal(str(sub_line.line_total)) == Decimal("240.00")

        # Quote must now be Converted
        reloaded_quote = db.session.get(Quotation, quote.id)
        assert reloaded_quote.status == QuotationStatus.CONVERTED


def test_unaccepted_quote_cannot_convert(db, app, customer, plan, product):
    """Draft quote cannot be converted to subscription without acceptance."""
    with app.app_context():
        quote = create_quotation(
            customer_id=customer.id,
            plan_id=plan.id,
            lines=[{"product_id": product.id, "quantity": 1, "unit_price": Decimal("100.00")}],
        )
        assert quote.status == QuotationStatus.DRAFT

        with pytest.raises(QuoteNotAcceptedError):
            quote_to_subscription(quote.id)


def test_already_converted_quote_cannot_convert_again(db, app, customer, plan, product):
    """Quotation cannot be converted twice."""
    with app.app_context():
        quote = create_quotation(
            customer_id=customer.id,
            plan_id=plan.id,
            lines=[{"product_id": product.id, "quantity": 1, "unit_price": Decimal("100.00")}],
        )
        quote.status = QuotationStatus.ACCEPTED
        db.session.commit()

        quote_to_subscription(quote.id)

        with pytest.raises(QuoteAlreadyConvertedError):
            quote_to_subscription(quote.id)


# ─────────────────────────────────────────────────────────────
#  Test 18 & 19 — A customer asks for a better price
# ─────────────────────────────────────────────────────────────

def test_customer_price_request_submission_and_approval(db, app, customer, plan, product):
    """Customer submits request for better price; staff approves and line price updates."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=2, price="100.00",
                                  status=SubscriptionStatus.ACTIVE)
        line = sub.lines[0]

        # Customer requests price 85.00
        pr = submit_price_request(
            subscription_line_id=line.id,
            customer_id=customer.id,
            requested_price=Decimal("85.00"),
            customer_note="We have a tight Q4 budget",
        )
        assert pr.status == PriceRequestStatus.PENDING
        assert Decimal(str(pr.requested_price)) == Decimal("85.00")

        # Staff approves at counter-price 90.00
        approved_pr = approve_price_request(
            price_request_id=pr.id,
            approved_price=Decimal("90.00"),
            staff_note="Approved at ₹90 counter-offer",
        )
        assert approved_pr.status == PriceRequestStatus.APPROVED
        assert Decimal(str(approved_pr.approved_price)) == Decimal("90.00")

        # Verify subscription line unit price and line_total updated
        reloaded_line = db.session.get(SubscriptionLine, line.id)
        assert Decimal(str(reloaded_line.unit_price)) == Decimal("90.00")
        assert Decimal(str(reloaded_line.line_total)) == Decimal("180.00")  # 2 * 90


def test_price_request_rejected(db, app, customer, plan, product):
    """Staff rejects customer price request; subscription line price remains unchanged."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.ACTIVE)
        line = sub.lines[0]

        pr = submit_price_request(
            subscription_line_id=line.id,
            customer_id=customer.id,
            requested_price=Decimal("70.00"),
            customer_note="Big discount please",
        )

        rejected_pr = reject_price_request(
            price_request_id=pr.id,
            staff_note="Cannot discount single license below list",
        )
        assert rejected_pr.status == PriceRequestStatus.REJECTED

        # Subscription line must remain 100.00
        reloaded_line = db.session.get(SubscriptionLine, line.id)
        assert Decimal(str(reloaded_line.unit_price)) == Decimal("100.00")


def test_price_request_validation(db, app, customer, plan, product):
    """Requested price must be less than current price and non-negative."""
    with app.app_context():
        sub = _make_subscription(db, customer, plan, product, qty=1, price="100.00",
                                  status=SubscriptionStatus.ACTIVE)
        line = sub.lines[0]

        # Equal or higher price rejected
        with pytest.raises(PriceRequestError):
            submit_price_request(line.id, customer.id, Decimal("100.00"))

        with pytest.raises(PriceRequestError):
            submit_price_request(line.id, customer.id, Decimal("120.00"))

        # Negative price rejected
        with pytest.raises(PriceRequestError):
            submit_price_request(line.id, customer.id, Decimal("-10.00"))


# ─────────────────────────────────────────────────────────────
#  Test 23 — Customer can request a quote
# ─────────────────────────────────────────────────────────────

def test_customer_can_request_quote(db, app, customer, plan, product):
    """Customer submits a custom quote request via client."""
    with app.test_client() as client:
        # Simulate customer login
        with client.session_transaction() as sess:
            sess["_user_id"] = str(customer.id)

        response = client.post(
            "/quotes/request",
            data={
                "plan_id": str(plan.id),
                "notes": "Looking for special pricing for 10 users",
                "product_id[]": [str(product.id)],
                "quantity[]": ["10"],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            # Check quote was created in Draft for this customer
            quo = Quotation.query.filter_by(customer_id=customer.id).order_by(Quotation.id.desc()).first()
            assert quo is not None
            assert quo.status == QuotationStatus.DRAFT
            assert quo.quotation_number.startswith("QUO-")
            assert len(quo.lines) == 1
            assert quo.lines[0].quantity == 10
            assert "Looking for special pricing" in quo.notes



