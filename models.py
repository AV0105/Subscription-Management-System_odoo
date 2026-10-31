"""
models.py
=========
ALL shared SQLAlchemy models for the Brightloop application.

Ownership:
  Person 1  — User, Product, Plan
  Person 2  — Subscription, SubscriptionLine, Invoice, InvoiceLine
  Person 3  — Payment

IMPORTANT: There is ONE db instance imported from extensions.py.
           Do NOT create a second SQLAlchemy() anywhere.
"""

from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date, timedelta
import enum

from flask_login import UserMixin
from extensions import db


# ─────────────────────────────────────────────────────────────
#  PERSON 1 — Foundation models
#  Person 1: fill in / adjust these to match your implementation.
#  These stubs exist so Person 2's relationships compile before
#  Person 1 commits their finished version.
# ─────────────────────────────────────────────────────────────

class RoleStr(str):
    """String wrapper that supports both string comparison and .value access."""
    @property
    def value(self):
        return str(self)


class Role(str, enum.Enum):
    """Simple role enumeration. Person 1 can expand this."""
    ADMIN = "Admin"
    STAFF = "Staff"
    CUSTOMER = "Customer"


class User(UserMixin, db.Model):
    """
    Application user — acts as both staff/admin AND customer.

    Person 1 owns this model.
    Compatible with Person 1's VARCHAR(20) role column while supporting .value.
    """
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    _role = db.Column("role", db.String(20), default="Customer", nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def role(self):
        return RoleStr(self._role or "Customer")

    @role.setter
    def role(self, val):
        self._role = val.value if hasattr(val, "value") else str(val)

    # Flask-Login requirement
    def get_id(self):
        return str(self.id)

    @property
    def is_authenticated(self):
        return True

    @property
    def is_anonymous(self):
        return False

    # Relationships (back-populated by Person 2)
    subscriptions = db.relationship("Subscription", back_populates="customer",
                                    lazy="dynamic")
    invoices = db.relationship("Invoice", back_populates="customer",
                               lazy="dynamic")
    # Person 3 adds: payments = ...

    def __repr__(self):
        return f"<User {self.email}>"


class Product(db.Model):
    """
    Sellable product / service.

    Person 1 owns this model.
    Stub provided so SubscriptionLine foreign key compiles.
    """
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    product_type = db.Column(db.String(50), default="Service")
    sales_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    cost_price = db.Column(db.Numeric(12, 2), default=0)
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    subscription_lines = db.relationship("SubscriptionLine", back_populates="product",
                                         lazy="dynamic")
    invoice_lines = db.relationship("InvoiceLine", back_populates="product",
                                    lazy="dynamic")

    def __repr__(self):
        return f"<Product {self.name}>"


class Plan(db.Model):
    """
    Subscription plan / billing tier.

    Person 1 owns this model.
    Stub provided so Subscription foreign key compiles.
    """
    __tablename__ = "plans"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    billing_frequency = db.Column(db.String(50), default="Monthly")  # Monthly / Annual / etc.
    minimum_quantity = db.Column(db.Integer, default=1)
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)
    auto_close = db.Column(db.Boolean, default=False)
    allow_renewal = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    @property
    def min_quantity(self):
        return self.minimum_quantity

    @min_quantity.setter
    def min_quantity(self, val):
        self.minimum_quantity = val

    # Relationships
    subscriptions = db.relationship("Subscription", back_populates="plan",
                                    lazy="dynamic")

    def __repr__(self):
        return f"<Plan {self.name}>"


# ─────────────────────────────────────────────────────────────
#  PERSON 2 — Subscription & Invoice models
# ─────────────────────────────────────────────────────────────

class SubscriptionStatus(str, enum.Enum):
    """Lifecycle states for a subscription."""
    DRAFT = "Draft"
    CONFIRMED = "Confirmed"
    ACTIVE = "Active"
    CLOSED = "Closed"


class Subscription(db.Model):
    """
    A customer's subscription to a plan.

    Lifecycle: Draft → Confirmed → Active → Closed
    """
    __tablename__ = "subscriptions"

    id = db.Column(db.Integer, primary_key=True)

    # Human-readable unique number, e.g. SUB-00001
    subscription_number = db.Column(db.String(20), unique=True, nullable=False, index=True)

    # Foreign keys
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    plan_id = db.Column(db.Integer, db.ForeignKey("plans.id"), nullable=False, index=True)

    # Dates
    start_date = db.Column(db.Date, nullable=False)
    expiry_date = db.Column(db.Date, nullable=True)

    # Status
    status = db.Column(
        db.Enum(SubscriptionStatus),
        default=SubscriptionStatus.DRAFT,
        nullable=False,
        index=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    customer = db.relationship("User", back_populates="subscriptions")
    plan = db.relationship("Plan", back_populates="subscriptions")
    lines = db.relationship(
        "SubscriptionLine",
        back_populates="subscription",
        cascade="all, delete-orphan",
        lazy="select",
    )
    invoices = db.relationship("Invoice", back_populates="subscription", lazy="dynamic")

    # ----------------------------------------------------------------
    # Computed properties & cross-compatibility aliases
    # ----------------------------------------------------------------

    @property
    def user_id(self):
        """Alias for Person 1 / 3 compatibility."""
        return self.customer_id

    @user_id.setter
    def user_id(self, val):
        self.customer_id = val

    @property
    def end_date(self):
        """Alias for Person 1 compatibility."""
        return self.expiry_date

    @end_date.setter
    def end_date(self, val):
        self.expiry_date = val

    @property
    def subtotal(self) -> Decimal:
        """Sum of all line totals using Decimal arithmetic."""
        return sum((line.line_total for line in self.lines), Decimal("0.00"))

    @property
    def is_billable(self) -> bool:
        """
        A subscription can only generate invoices when it is
        Confirmed or Active (not Draft, not Closed).
        """
        return self.status in (SubscriptionStatus.CONFIRMED, SubscriptionStatus.ACTIVE)

    def __repr__(self):
        return f"<Subscription {self.subscription_number}>"


class SubscriptionLine(db.Model):
    """
    One product line within a subscription.

    IMPORTANT: unit_price is a SNAPSHOT of the price at line-creation
    time.  Do not recalculate from Product.sales_price later.
    """
    __tablename__ = "subscription_lines"

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(
        db.Integer, db.ForeignKey("subscriptions.id"), nullable=False, index=True
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False, index=True
    )

    quantity = db.Column(db.Integer, nullable=False, default=1)
    # Snapshot of price at the time the line was created
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    # Calculated and stored: quantity × unit_price
    line_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    # Relationships
    subscription = db.relationship("Subscription", back_populates="lines")
    product = db.relationship("Product", back_populates="subscription_lines")

    def calculate_total(self):
        """
        Recalculate line_total from quantity and unit_price using Decimal.
        Always call this before saving a line.
        """
        qty = Decimal(str(self.quantity))
        price = Decimal(str(self.unit_price))
        self.line_total = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def __repr__(self):
        return f"<SubscriptionLine sub={self.subscription_id} product={self.product_id}>"


# ─────────────────────────────────────────────────────────────
#  Invoice
# ─────────────────────────────────────────────────────────────

class InvoiceStatus(str, enum.Enum):
    """Lifecycle states for an invoice."""
    DRAFT = "Draft"
    CONFIRMED = "Confirmed"
    PAID = "Paid"
    CANCELLED = "Cancelled"


class Invoice(db.Model):
    """
    Invoice generated from a Subscription.

    Values are SNAPSHOTS — changing a Product price later must NOT
    alter an existing invoice.

    One invoice per subscription (MVP).  Enforced by the unique
    constraint on subscription_id AND by generate_invoice() logic.
    """
    __tablename__ = "invoices"

    id = db.Column(db.Integer, primary_key=True)

    # Human-readable unique number, e.g. INV-00001
    invoice_number = db.Column(db.String(20), unique=True, nullable=False, index=True)

    # Foreign keys
    # One invoice per subscription for MVP (unique constraint)
    subscription_id = db.Column(
        db.Integer,
        db.ForeignKey("subscriptions.id"),
        nullable=False,
        unique=True,          # DATABASE-LEVEL duplicate prevention
        index=True,
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )

    # Dates
    issue_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=False)

    # Monetary fields — all stored as DECIMAL, calculated using Decimal
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    # Status
    status = db.Column(
        db.Enum(InvoiceStatus),
        default=InvoiceStatus.DRAFT,
        nullable=False,
        index=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    customer = db.relationship("User", back_populates="invoices")
    subscription = db.relationship("Subscription", back_populates="invoices")
    lines = db.relationship(
        "InvoiceLine",
        back_populates="invoice",
        cascade="all, delete-orphan",
        lazy="select",
    )
    # Person 3 adds: payments = db.relationship("Payment", ...)

    # ----------------------------------------------------------------
    # Cross-compatibility aliases for Person 1 / 3
    # ----------------------------------------------------------------
    @property
    def user_id(self):
        return self.customer_id

    @user_id.setter
    def user_id(self, val):
        self.customer_id = val

    @property
    def amount(self):
        return self.total

    @amount.setter
    def amount(self, val):
        self.total = val

    def __repr__(self):
        return f"<Invoice {self.invoice_number}>"


class InvoiceLine(db.Model):
    """
    One line item on an Invoice — a SNAPSHOT of the subscription line
    at billing time.

    Once created, changing Product data must NOT alter these values.
    """
    __tablename__ = "invoice_lines"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.Integer, db.ForeignKey("invoices.id"), nullable=False, index=True
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=True, index=True
    )

    # Description snapshot — store product name at invoice time
    description = db.Column(db.String(300), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    line_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    # Relationships
    invoice = db.relationship("Invoice", back_populates="lines")
    product = db.relationship("Product", back_populates="invoice_lines")

    def __repr__(self):
        return f"<InvoiceLine invoice={self.invoice_id} desc={self.description!r}>"


# ─────────────────────────────────────────────────────────────
#  PERSON 3 — Payment model stub
#  Person 3 will expand/replace this stub.
# ─────────────────────────────────────────────────────────────

class Payment(db.Model):
    """
    Payment recorded against an Invoice.
    Person 3 owns this model — stub provided for FK compilation.
    """
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.Integer, db.ForeignKey("invoices.id"), nullable=False, index=True
    )
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payment_method = db.Column(db.String(50))
    payment_date = db.Column(db.Date, nullable=False, default=date.today)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship (Person 3 adds back_populates on Invoice)
    invoice = db.relationship("Invoice", backref="payments")

    def __repr__(self):
        return f"<Payment invoice={self.invoice_id} amount={self.amount}>"

