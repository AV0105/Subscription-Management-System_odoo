"""
subscription_routes.py
======================
Flask Blueprint for Subscription and Invoice routes.

Person 2 owns this file.

Routes
------
GET  /subscriptions                  — list subscriptions
GET  /subscriptions/new              — new subscription form
POST /subscriptions/new              — create subscription
GET  /subscriptions/<id>             — subscription detail
POST /subscriptions/<id>/confirm     — confirm (Draft → Confirmed)
POST /subscriptions/<id>/activate    — activate (Confirmed → Active)
POST /subscriptions/<id>/close       — close subscription (→ Closed)
POST /subscriptions/<id>/generate-invoice  — generate invoice

GET  /invoices                       — list invoices
GET  /invoices/<id>                  — invoice detail
POST /invoices/<id>/confirm          — confirm invoice (Draft → Confirmed)
POST /invoices/<id>/cancel           — cancel invoice
"""

import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta

from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request,
    abort,
    current_app,
)
from flask_login import login_required, current_user

import uuid
from models import Product, Plan, SubscriptionStatus # Add SubscriptionStatus

from extensions import db
from models import (
    User,
    Product,
    Plan,
    Subscription,
    SubscriptionLine,
    SubscriptionStatus,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Role,
)
from invoice_service import (
    generate_invoice,
    InvoiceAlreadyExistsError,
    SubscriptionNotBillableError,
    _next_subscription_number,
    _to_decimal,
)

logger = logging.getLogger(__name__)

subscription_bp = Blueprint("subscriptions", __name__, url_prefix="")

TWO_PLACES = Decimal("0.01")


# ─────────────────────────────────────────────────────────────
#  Helper: role guard (works with Person 1's Role enum)
# ─────────────────────────────────────────────────────────────

def _require_staff_or_admin():
    """Return 403 if the logged-in user is a plain Customer."""
    if current_user.role == Role.CUSTOMER:
        abort(403)


# ─────────────────────────────────────────────────────────────
#  SUBSCRIPTION — list
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/subscriptions")
@login_required
def subscriptions_list():
    """Show all subscriptions (admin/staff) or own subscriptions (customer)."""
    if current_user.role == Role.CUSTOMER:
        subs = (
            Subscription.query
            .filter_by(customer_id=current_user.id)
            .order_by(Subscription.created_at.desc())
            .all()
        )
    else:
        subs = (
            Subscription.query
            .order_by(Subscription.created_at.desc())
            .all()
        )
    return render_template("subscriptions.html", subscriptions=subs)


# ─────────────────────────────────────────────────────────────
#  SUBSCRIPTION — create
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/subscriptions/new", methods=["GET", "POST"])
@login_required
def subscription_new():
    """Create a new subscription."""
    _require_staff_or_admin()

    customers = User.query.filter_by(is_active=True).order_by(User.name).all()
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.name).all()
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if request.method == "POST":
        errors = []

        # ── Basic fields ─────────────────────────────────────
        customer_id = request.form.get("customer_id", "").strip()
        plan_id = request.form.get("plan_id", "").strip()
        start_date_str = request.form.get("start_date", "").strip()
        expiry_date_str = request.form.get("expiry_date", "").strip()

        # Validate customer
        if not customer_id:
            errors.append("Customer is required.")
        else:
            customer = User.query.get(int(customer_id))
            if customer is None:
                errors.append("Selected customer does not exist.")

        # Validate plan
        if not plan_id:
            errors.append("Plan is required.")
        else:
            plan = Plan.query.get(int(plan_id))
            if plan is None:
                errors.append("Selected plan does not exist.")

        # Validate start date
        start_date = None
        if not start_date_str:
            errors.append("Start date is required.")
        else:
            try:
                start_date = date.fromisoformat(start_date_str)
            except ValueError:
                errors.append("Start date is not a valid date.")

        # Validate expiry date (optional but if provided must be after start)
        expiry_date = None
        if expiry_date_str:
            try:
                expiry_date = date.fromisoformat(expiry_date_str)
                if start_date and expiry_date <= start_date:
                    errors.append("Expiry date must be after start date.")
            except ValueError:
                errors.append("Expiry date is not a valid date.")

        # ── Line items ────────────────────────────────────────
        product_ids = request.form.getlist("product_id[]")
        quantities = request.form.getlist("quantity[]")
        unit_prices = request.form.getlist("unit_price[]")

        # Filter out empty rows (user may have added blank rows)
        line_data = []
        for pid, qty_str, price_str in zip(product_ids, quantities, unit_prices):
            if not pid:
                continue
            line_data.append((pid, qty_str, price_str))

        if not line_data:
            errors.append("At least one product line is required.")

        parsed_lines = []
        for pid, qty_str, price_str in line_data:
            product_obj = Product.query.get(int(pid))
            if product_obj is None:
                errors.append(f"Product with id={pid} does not exist.")
                continue

            try:
                qty = int(qty_str)
            except (ValueError, TypeError):
                errors.append(f"Quantity for '{product_obj.name}' must be a whole number.")
                continue

            if qty <= 0:
                errors.append(f"Quantity for '{product_obj.name}' must be greater than zero.")
                continue

            try:
                price = _to_decimal(price_str)
            except Exception:
                errors.append(f"Unit price for '{product_obj.name}' is not a valid number.")
                continue

            if price < Decimal("0.00"):
                errors.append(f"Unit price for '{product_obj.name}' cannot be negative.")
                continue

            parsed_lines.append((product_obj, qty, price))

        # ── If errors → re-render form ────────────────────────
        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "subscription_form.html",
                customers=customers,
                plans=plans,
                products=products,
                form_data=request.form,
            )

        # ── Build subscription ────────────────────────────────
        try:
            sub_number = _next_subscription_number()
            subscription = Subscription(
                subscription_number=sub_number,
                customer_id=int(customer_id),
                plan_id=int(plan_id),
                start_date=start_date,
                expiry_date=expiry_date,
                status=SubscriptionStatus.DRAFT,
            )
            db.session.add(subscription)
            db.session.flush()  # get subscription.id before adding lines

            for product_obj, qty, price in parsed_lines:
                line = SubscriptionLine(
                    subscription_id=subscription.id,
                    product_id=product_obj.id,
                    quantity=qty,
                    unit_price=price,
                )
                line.calculate_total()
                db.session.add(line)

            db.session.commit()
            flash(
                f"Subscription {subscription.subscription_number} created successfully.",
                "success",
            )
            return redirect(url_for("subscriptions.subscription_detail", sub_id=subscription.id))

        except Exception as exc:
            db.session.rollback()
            logger.error("Error creating subscription: %s", exc)
            flash(f"Unable to create subscription: {exc}", "danger")

    return render_template(
        "subscription_form.html",
        customers=customers,
        plans=plans,
        products=products,
        form_data={},
    )


# ─────────────────────────────────────────────────────────────
#  SUBSCRIPTION — detail
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/subscriptions/<int:sub_id>")
@login_required
def subscription_detail(sub_id):
    """Show subscription detail, lines, and related invoices."""
    subscription = Subscription.query.get_or_404(sub_id)

    # Customers can only see their own
    if current_user.role == Role.CUSTOMER and subscription.customer_id != current_user.id:
        abort(403)

    return render_template("subscription_detail.html", subscription=subscription)


# ─────────────────────────────────────────────────────────────
#  SUBSCRIPTION — lifecycle transitions
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/subscriptions/<int:sub_id>/confirm", methods=["POST"])
@login_required
def subscription_confirm(sub_id):
    """Transition: Draft → Confirmed."""
    _require_staff_or_admin()
    subscription = Subscription.query.get_or_404(sub_id)

    if subscription.status != SubscriptionStatus.DRAFT:
        flash(
            f"Cannot confirm a subscription that is '{subscription.status.value}'.", "warning"
        )
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))

    if not subscription.lines:
        flash("Cannot confirm a subscription with no line items.", "danger")
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))

    subscription.status = SubscriptionStatus.CONFIRMED
    db.session.commit()
    flash(f"Subscription {subscription.subscription_number} confirmed.", "success")
    return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))


@subscription_bp.route("/subscriptions/<int:sub_id>/activate", methods=["POST"])
@login_required
def subscription_activate(sub_id):
    """Transition: Confirmed → Active."""
    _require_staff_or_admin()
    subscription = Subscription.query.get_or_404(sub_id)

    if subscription.status != SubscriptionStatus.CONFIRMED:
        flash(
            f"Cannot activate a subscription that is '{subscription.status.value}'. "
            "It must be Confirmed first.",
            "warning",
        )
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))

    subscription.status = SubscriptionStatus.ACTIVE
    db.session.commit()
    flash(f"Subscription {subscription.subscription_number} is now Active.", "success")
    return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))


@subscription_bp.route("/subscriptions/<int:sub_id>/close", methods=["POST"])
@login_required
def subscription_close(sub_id):
    """Transition: Any → Closed."""
    _require_staff_or_admin()
    subscription = Subscription.query.get_or_404(sub_id)

    if subscription.status == SubscriptionStatus.CLOSED:
        flash("Subscription is already closed.", "info")
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))

    subscription.status = SubscriptionStatus.CLOSED
    db.session.commit()
    flash(f"Subscription {subscription.subscription_number} has been closed.", "success")
    return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))


# ─────────────────────────────────────────────────────────────
#  SUBSCRIPTION → INVOICE generation
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/subscriptions/<int:sub_id>/generate-invoice", methods=["POST"])
@login_required
def subscription_generate_invoice(sub_id):
    """Generate an invoice from the subscription and redirect to it."""
    _require_staff_or_admin()

    try:
        invoice = generate_invoice(sub_id)
        flash(
            f"Invoice {invoice.invoice_number} generated successfully.", "success"
        )
        return redirect(url_for("subscriptions.invoice_detail", inv_id=invoice.id))

    except InvoiceAlreadyExistsError as exc:
        flash(str(exc), "warning")
        # Redirect to the existing invoice
        existing = Invoice.query.filter_by(subscription_id=sub_id).first()
        if existing:
            return redirect(url_for("subscriptions.invoice_detail", inv_id=existing.id))
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))

    except SubscriptionNotBillableError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))

    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))

    except RuntimeError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))


# ─────────────────────────────────────────────────────────────
#  INVOICE — list
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/invoices")
@login_required
def invoices_list():
    """List invoices — all for staff/admin, own for customer."""
    if current_user.role == Role.CUSTOMER:
        invs = (
            Invoice.query
            .filter_by(customer_id=current_user.id)
            .order_by(Invoice.created_at.desc())
            .all()
        )
    else:
        invs = Invoice.query.order_by(Invoice.created_at.desc()).all()

    return render_template("invoices.html", invoices=invs)


# ─────────────────────────────────────────────────────────────
#  INVOICE — detail
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/invoices/<int:inv_id>")
@login_required
def invoice_detail(inv_id):
    """Invoice detail view with line items."""
    invoice = Invoice.query.get_or_404(inv_id)

    if current_user.role == Role.CUSTOMER and invoice.customer_id != current_user.id:
        abort(403)

    return render_template("invoice_detail.html", invoice=invoice)


# ─────────────────────────────────────────────────────────────
#  INVOICE — lifecycle transitions
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/invoices/<int:inv_id>/confirm", methods=["POST"])
@login_required
def invoice_confirm(inv_id):
    """Transition invoice: Draft → Confirmed."""
    _require_staff_or_admin()
    invoice = Invoice.query.get_or_404(inv_id)

    if invoice.status != InvoiceStatus.DRAFT:
        flash(f"Invoice is already '{invoice.status.value}'.", "warning")
        return redirect(url_for("subscriptions.invoice_detail", inv_id=inv_id))

    invoice.status = InvoiceStatus.CONFIRMED
    db.session.commit()
    flash(f"Invoice {invoice.invoice_number} confirmed.", "success")
    return redirect(url_for("subscriptions.invoice_detail", inv_id=inv_id))


@subscription_bp.route("/invoices/<int:inv_id>/cancel", methods=["POST"])
@login_required
def invoice_cancel(inv_id):
    """Cancel an invoice (Draft or Confirmed only)."""
    _require_staff_or_admin()
    invoice = Invoice.query.get_or_404(inv_id)

    if invoice.status in (InvoiceStatus.PAID, InvoiceStatus.CANCELLED):
        flash(
            f"Cannot cancel an invoice that is already '{invoice.status.value}'.", "warning"
        )
        return redirect(url_for("subscriptions.invoice_detail", inv_id=inv_id))

    invoice.status = InvoiceStatus.CANCELLED
    db.session.commit()
    flash(f"Invoice {invoice.invoice_number} has been cancelled.", "success")
    return redirect(url_for("subscriptions.invoices_list"))

@subscription_bp.route('/my-portal')
@login_required
def my_portal():
    """Customer portal: shows only the logged-in user's data."""
    my_subs = Subscription.query.filter_by(customer_id=current_user.id).all()
    my_invoices = Invoice.query.filter_by(customer_id=current_user.id).order_by(Invoice.issue_date.desc()).all()
    
    return render_template(
        'customer_portal.html', 
        subscriptions=my_subs, 
        invoices=my_invoices
    )

# --- CUSTOMER MARKETPLACE ---
@subscription_bp.route('/marketplace')
@login_required
def marketplace():
    """Customers browse available products and plans."""
    products = Product.query.all()
    plans = Plan.query.all()
    return render_template('marketplace.html', products=products, plans=plans)

# --- CUSTOMER SUBSCRIBE ACTION ---
@subscription_bp.route('/subscribe', methods=['POST'])
@login_required
def customer_subscribe():
    product_id = request.form.get('product_id', type=int)
    plan_id = request.form.get('plan_id', type=int)

    product = Product.query.get_or_404(product_id)
    plan = Plan.query.get_or_404(plan_id)

    # Generate unique subscription number
    sub_number = f"SUB-{uuid.uuid4().hex[:6].upper()}"
    today = date.today()
    
    # Safely handle Enum status
    try:
        active_status = SubscriptionStatus.ACTIVE
    except Exception:
        active_status = 'Active'

    # 1. Create the Subscription
    new_sub = Subscription(
        subscription_number=sub_number,
        customer_id=current_user.id,
        plan_id=plan.id,
        start_date=today,
        expiry_date=today + timedelta(days=30), # MVP: Default 30-day billing cycle
        status=active_status
    )
    db.session.add(new_sub)
    db.session.flush() # Gets the new_sub.id before committing

    # 2. Create the Subscription Line Item
    qty = 1
    price = product.sales_price
    line_total = qty * price

    new_line = SubscriptionLine(
        subscription_id=new_sub.id,
        product_id=product.id,
        quantity=qty,
        unit_price=price,
        line_total=line_total
    )
    db.session.add(new_line)
    db.session.commit()

    flash(f'Successfully subscribed to {product.name}!', 'success')
    return redirect(url_for('subscriptions.my_portal'))

# --- CUSTOMER UNSUBSCRIBE ACTION ---
@subscription_bp.route('/unsubscribe/<int:sub_id>', methods=['POST'])
@login_required
def customer_unsubscribe(sub_id):
    sub = Subscription.query.get_or_404(sub_id)
    
    # Security: Ensure customer can only cancel their OWN subscription
    if sub.customer_id != current_user.id:
        abort(403) 
        
    try:
        sub.status = SubscriptionStatus.CLOSED
    except Exception:
        sub.status = 'Closed'
        
    db.session.commit()
    flash('Your subscription has been cancelled.', 'warning')
    return redirect(url_for('subscriptions.my_portal'))