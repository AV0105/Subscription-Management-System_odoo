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

    # Billing day of month (1-28).  None = bill on demand (today).
    # e.g. billing_day=1  → always invoice on the 1st of the month
    # e.g. billing_day=15 → always invoice on the 15th
    billing_day = db.Column(db.Integer, nullable=True)

    # Source quotation this subscription was created from (nullable)
    quotation_id = db.Column(
        db.Integer, db.ForeignKey("quotations.id"), nullable=True, index=True
    )

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
    quotation_source = db.relationship("Quotation", back_populates="subscription", foreign_keys="Subscription.quotation_id")

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

    def next_billing_date(self) -> date:
        """
        Return the next invoice issue date respecting billing_day.

        If billing_day is set (1-28), the issue date is the nearest upcoming
        occurrence of that day-of-month from today.
        If billing_day is None, fall back to today.
        """
        today = date.today()
        if not self.billing_day:
            return today

        day = self.billing_day
        # Try current month first
        try:
            candidate = today.replace(day=day)
        except ValueError:
            # Day doesn't exist in this month (e.g. 30 in February) → use last day
            import calendar
            last = calendar.monthrange(today.year, today.month)[1]
            candidate = today.replace(day=last)

        if candidate >= today:
            return candidate

        # Move to next month
        if today.month == 12:
            return date(today.year + 1, 1, min(day, 28))
        else:
            next_month = today.month + 1
            import calendar
            last = calendar.monthrange(today.year, next_month)[1]
            return date(today.year, next_month, min(day, last))

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
    price_requests = db.relationship("PriceRequest", back_populates="subscription_line", lazy="dynamic")

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
#  PERSON 2 — Quotation (Quote → Subscription)
# ─────────────────────────────────────────────────────────────

class QuotationStatus(str, enum.Enum):
    """Lifecycle states for a Quotation / Quote."""
    DRAFT     = "Draft"
    SENT      = "Sent"       # Sent to customer
    ACCEPTED  = "Accepted"   # Customer accepted → can convert to Subscription
    REJECTED  = "Rejected"
    CONVERTED = "Converted"  # Already turned into a Subscription


class Quotation(db.Model):
    """
    A price quote sent to a prospective customer before a Subscription is created.

    Lifecycle: Draft → Sent → Accepted → Converted
                                       ↘ Rejected

    Once Accepted, staff click "Convert to Subscription" which calls
    quote_to_subscription() in quote_service.py.
    """
    __tablename__ = "quotations"

    id = db.Column(db.Integer, primary_key=True)

    # Human-readable number, e.g. QUO-00001
    quotation_number = db.Column(db.String(20), unique=True, nullable=False, index=True)

    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    plan_id     = db.Column(db.Integer, db.ForeignKey("plans.id"), nullable=False, index=True)

    # Quote validity window
    valid_from  = db.Column(db.Date, nullable=True)
    valid_until = db.Column(db.Date, nullable=True)

    # Optional note / sales pitch to show the customer
    notes = db.Column(db.Text, nullable=True)

    status = db.Column(
        db.Enum(QuotationStatus),
        default=QuotationStatus.DRAFT,
        nullable=False,
        index=True,
    )

    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    customer = db.relationship("User", foreign_keys=[customer_id])
    plan     = db.relationship("Plan")
    lines    = db.relationship(
        "QuotationLine",
        back_populates="quotation",
        cascade="all, delete-orphan",
        lazy="select",
    )
    # Reverse: the subscription that was born from this quote
    subscription = db.relationship(
        "Subscription",
        back_populates="quotation_source",
        foreign_keys="Subscription.quotation_id",
        uselist=False,
    )

    @property
    def subtotal(self) -> Decimal:
        return sum((line.line_total for line in self.lines), Decimal("0.00"))

    @property
    def is_expired(self) -> bool:
        if self.valid_until is None:
            return False
        return date.today() > self.valid_until

    def __repr__(self):
        return f"<Quotation {self.quotation_number}>"


class QuotationLine(db.Model):
    """One product line on a Quotation."""
    __tablename__ = "quotation_lines"

    id           = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey("quotations.id"), nullable=False, index=True)
    product_id   = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)

    quantity   = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    line_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    quotation = db.relationship("Quotation", back_populates="lines")
    product   = db.relationship("Product")

    def calculate_total(self):
        qty   = Decimal(str(self.quantity))
        price = Decimal(str(self.unit_price))
        self.line_total = (qty * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def __repr__(self):
        return f"<QuotationLine q={self.quotation_id} p={self.product_id}>"


# ─────────────────────────────────────────────────────────────
#  PERSON 2 — Customer Price Request
# ─────────────────────────────────────────────────────────────

class PriceRequestStatus(str, enum.Enum):
    """Status of a customer's request for a better price."""
    PENDING  = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


class PriceRequest(db.Model):
    """
    A customer asks for a better unit price on a SubscriptionLine.

    Workflow:
      Customer clicks "Request Better Price" on their subscription line.
      Staff sees the request in the admin area.
      Staff approves (with an optional counter-offer price) or rejects.
      On Approval: the subscription line's unit_price and line_total are
                   updated atomically.
    """
    __tablename__ = "price_requests"

    id = db.Column(db.Integer, primary_key=True)

    subscription_line_id = db.Column(
        db.Integer, db.ForeignKey("subscription_lines.id"), nullable=False, index=True
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )

    # Price the customer is currently paying
    current_price = db.Column(db.Numeric(12, 2), nullable=False)
    # Price the customer is requesting
    requested_price = db.Column(db.Numeric(12, 2), nullable=False)
    # Price staff actually approves (may differ from request — counter-offer)
    approved_price = db.Column(db.Numeric(12, 2), nullable=True)

    # Customer's reason / message
    customer_note = db.Column(db.Text, nullable=True)
    # Staff's response note
    staff_note = db.Column(db.Text, nullable=True)

    status = db.Column(
        db.Enum(PriceRequestStatus),
        default=PriceRequestStatus.PENDING,
        nullable=False,
        index=True,
    )

    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    resolved_at  = db.Column(db.DateTime, nullable=True)

    # Relationships
    subscription_line = db.relationship("SubscriptionLine", back_populates="price_requests")
    customer          = db.relationship("User", foreign_keys=[customer_id])

    def __repr__(self):
        return f"<PriceRequest line={self.subscription_line_id} status={self.status.value}>"


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

