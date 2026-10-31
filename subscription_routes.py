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

GET  /quotes                         — list quotations
GET  /quotes/new                     — new quotation form
POST /quotes/new                     — create quotation
GET  /quotes/<id>                    — quotation detail
POST /quotes/<id>/send               — Draft → Sent
POST /quotes/<id>/accept             — Sent → Accepted
POST /quotes/<id>/reject             — Sent → Rejected
POST /quotes/<id>/convert            — Accepted → Subscription (Converted)

GET  /price-requests                 — list all price requests (staff)
POST /subscriptions/<sub_id>/lines/<line_id>/request-price  — customer submits
POST /price-requests/<id>/approve    — staff approves
POST /price-requests/<id>/reject     — staff rejects
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
    Quotation,
    QuotationLine,
    QuotationStatus,
    PriceRequest,
    PriceRequestStatus,
    Role,
)
from invoice_service import (
    generate_invoice,
    InvoiceAlreadyExistsError,
    SubscriptionNotBillableError,
    _next_subscription_number,
    _to_decimal,
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
        billing_day_str = request.form.get("billing_day", "").strip()

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

        # Validate billing day (optional, 1-28)
        billing_day = None
        if billing_day_str:
            try:
                billing_day = int(billing_day_str)
                if not (1 <= billing_day <= 28):
                    errors.append("Billing day of month must be between 1 and 28.")
            except (ValueError, TypeError):
                errors.append("Billing day must be a number between 1 and 28.")

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
                billing_day=billing_day,
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


# ─────────────────────────────────────────────────────────────
#  QUOTATION — routes (Turning a quote into a subscription)
# ─────────────────────────────────────────────────────────────

@subscription_bp.route("/quotes")
@login_required
def quotes_list():
    """List quotations."""
    if current_user.role == Role.CUSTOMER:
        quotes = (
            Quotation.query
            .filter_by(customer_id=current_user.id)
            .order_by(Quotation.created_at.desc())
            .all()
        )
    else:
        quotes = Quotation.query.order_by(Quotation.created_at.desc()).all()

    return render_template("quotes.html", quotes=quotes)


@subscription_bp.route("/quotes/request", methods=["GET", "POST"])
@login_required
def quote_request():
    """Customer-facing Request a Quote form (just like modern SaaS websites)."""
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.name).all()
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if request.method == "POST":
        errors = []
        plan_id = request.form.get("plan_id", "").strip()
        notes = request.form.get("notes", "").strip()

        if not plan_id:
            errors.append("Please select a subscription plan.")

        product_ids = request.form.getlist("product_id[]")
        quantities = request.form.getlist("quantity[]")

        lines = []
        for pid, qty_str in zip(product_ids, quantities):
            if not pid:
                continue
            product_obj = db.session.get(Product, int(pid))
            if not product_obj:
                continue
            try:
                qty = int(qty_str)
                if qty <= 0:
                    errors.append(f"Quantity for {product_obj.name} must be greater than zero.")
                    continue
                # Catalog sales price as baseline
                lines.append({
                    "product_id": product_obj.id,
                    "quantity": qty,
                    "unit_price": product_obj.sales_price,
                })
            except (ValueError, TypeError):
                errors.append(f"Invalid quantity for {product_obj.name}.")

        if not lines:
            errors.append("Please select at least one product.")

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "customer_quote_request.html",
                plans=plans,
                products=products,
                form_data=request.form,
            )

        try:
            quotation = create_quotation(
                customer_id=current_user.id,
                plan_id=int(plan_id),
                lines=lines,
                valid_from=date.today(),
                valid_until=date.today() + timedelta(days=14),
                notes=f"Customer Quote Request: {notes}" if notes else "Customer requested custom quote.",
            )
            flash(
                f"Your quote request ({quotation.quotation_number}) was submitted successfully! Our sales team will prepare your custom proposal.",
                "success",
            )
            return redirect(url_for("subscriptions.quote_detail", quo_id=quotation.id))
        except Exception as exc:
            flash(f"Unable to submit quote request: {exc}", "danger")

    return render_template(
        "customer_quote_request.html",
        plans=plans,
        products=products,
        form_data={},
    )


@subscription_bp.route("/quotes/new", methods=["GET", "POST"])
@login_required
def quote_new():
    """Create a new quotation."""
    _require_staff_or_admin()

    customers = User.query.filter_by(is_active=True).order_by(User.name).all()
    plans = Plan.query.filter_by(is_active=True).order_by(Plan.name).all()
    products = Product.query.filter_by(is_active=True).order_by(Product.name).all()

    if request.method == "POST":
        errors = []
        customer_id = request.form.get("customer_id", "").strip()
        plan_id = request.form.get("plan_id", "").strip()
        valid_from_str = request.form.get("valid_from", "").strip()
        valid_until_str = request.form.get("valid_until", "").strip()
        notes = request.form.get("notes", "").strip()

        if not customer_id:
            errors.append("Customer is required.")
        if not plan_id:
            errors.append("Plan is required.")

        valid_from = None
        if valid_from_str:
            try:
                valid_from = date.fromisoformat(valid_from_str)
            except ValueError:
                errors.append("Valid-from date is invalid.")

        valid_until = None
        if valid_until_str:
            try:
                valid_until = date.fromisoformat(valid_until_str)
                if valid_from and valid_until < valid_from:
                    errors.append("Valid-until date must be after valid-from date.")
            except ValueError:
                errors.append("Valid-until date is invalid.")

        product_ids = request.form.getlist("product_id[]")
        quantities = request.form.getlist("quantity[]")
        unit_prices = request.form.getlist("unit_price[]")

        lines = []
        for pid, qty_str, price_str in zip(product_ids, quantities, unit_prices):
            if not pid:
                continue
            try:
                qty = int(qty_str)
                price = _to_decimal(price_str)
                if qty <= 0:
                    errors.append("Line quantity must be greater than zero.")
                    continue
                if price < Decimal("0.00"):
                    errors.append("Line unit price cannot be negative.")
                    continue
                lines.append({"product_id": int(pid), "quantity": qty, "unit_price": price})
            except Exception as exc:
                errors.append(f"Invalid line values: {exc}")

        if not lines:
            errors.append("At least one product line item is required.")

        if errors:
            for msg in errors:
                flash(msg, "danger")
            return render_template(
                "quote_form.html",
                customers=customers,
                plans=plans,
                products=products,
                form_data=request.form,
            )

        try:
            quotation = create_quotation(
                customer_id=int(customer_id),
                plan_id=int(plan_id),
                lines=lines,
                valid_from=valid_from,
                valid_until=valid_until,
                notes=notes,
            )
            flash(f"Quotation {quotation.quotation_number} created successfully.", "success")
            return redirect(url_for("subscriptions.quote_detail", quo_id=quotation.id))
        except Exception as exc:
            flash(f"Error creating quotation: {exc}", "danger")

    return render_template(
        "quote_form.html",
        customers=customers,
        plans=plans,
        products=products,
        form_data={},
    )


@subscription_bp.route("/quotes/<int:quo_id>")
@login_required
def quote_detail(quo_id):
    """View quotation details."""
    quote = Quotation.query.get_or_404(quo_id)
    if current_user.role == Role.CUSTOMER and quote.customer_id != current_user.id:
        abort(403)
    return render_template("quote_detail.html", quote=quote)


@subscription_bp.route("/quotes/<int:quo_id>/send", methods=["POST"])
@login_required
def quote_send(quo_id):
    """Send quote to customer (Draft -> Sent)."""
    _require_staff_or_admin()
    quote = Quotation.query.get_or_404(quo_id)
    if quote.status != QuotationStatus.DRAFT:
        flash(f"Cannot send quotation that is {quote.status.value}.", "warning")
        return redirect(url_for("subscriptions.quote_detail", quo_id=quo_id))

    quote.status = QuotationStatus.SENT
    db.session.commit()
    flash(f"Quotation {quote.quotation_number} marked as Sent.", "success")
    return redirect(url_for("subscriptions.quote_detail", quo_id=quo_id))


@subscription_bp.route("/quotes/<int:quo_id>/accept", methods=["POST"])
@login_required
def quote_accept(quo_id):
    """Accept quote (Customer or Staff)."""
    quote = Quotation.query.get_or_404(quo_id)
    if current_user.role == Role.CUSTOMER and quote.customer_id != current_user.id:
        abort(403)

    if quote.status in (QuotationStatus.ACCEPTED, QuotationStatus.CONVERTED):
        flash(f"Quotation is already {quote.status.value}.", "info")
        return redirect(url_for("subscriptions.quote_detail", quo_id=quo_id))

    quote.status = QuotationStatus.ACCEPTED
    db.session.commit()
    flash(f"Quotation {quote.quotation_number} accepted! Ready to convert to subscription.", "success")
    return redirect(url_for("subscriptions.quote_detail", quo_id=quo_id))


@subscription_bp.route("/quotes/<int:quo_id>/reject", methods=["POST"])
@login_required
def quote_reject(quo_id):
    """Reject quote."""
    quote = Quotation.query.get_or_404(quo_id)
    if current_user.role == Role.CUSTOMER and quote.customer_id != current_user.id:
        abort(403)

    quote.status = QuotationStatus.REJECTED
    db.session.commit()
    flash(f"Quotation {quote.quotation_number} rejected.", "info")
    return redirect(url_for("subscriptions.quote_detail", quo_id=quo_id))


@subscription_bp.route("/quotes/<int:quo_id>/convert", methods=["POST"])
@login_required
def quote_convert(quo_id):
    """Turn quotation into a subscription."""
    _require_staff_or_admin()
    start_date_str = request.form.get("start_date", "").strip()
    expiry_date_str = request.form.get("expiry_date", "").strip()
    billing_day_str = request.form.get("billing_day", "").strip()

    start_date = date.today()
    if start_date_str:
        try:
            start_date = date.fromisoformat(start_date_str)
        except ValueError:
            pass

    expiry_date = None
    if expiry_date_str:
        try:
            expiry_date = date.fromisoformat(expiry_date_str)
        except ValueError:
            pass

    billing_day = None
    if billing_day_str:
        try:
            billing_day = int(billing_day_str)
            if not (1 <= billing_day <= 28):
                billing_day = None
        except ValueError:
            pass

    try:
        sub = quote_to_subscription(
            quotation_id=quo_id,
            start_date=start_date,
            expiry_date=expiry_date,
            billing_day=billing_day,
        )
        flash(
            f"Successfully converted Quote to Subscription {sub.subscription_number}!",
            "success",
        )
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub.id))
    except (QuoteAlreadyConvertedError, QuoteNotAcceptedError, ValueError, RuntimeError) as exc:
        flash(str(exc), "danger")
        return redirect(url_for("subscriptions.quote_detail", quo_id=quo_id))


# ─────────────────────────────────────────────────────────────
#  CUSTOMER PRICE REQUESTS (A customer asks for a better price)
# ─────────────────────────────────────────────────────────────

@subscription_bp.route(
    "/subscriptions/<int:sub_id>/lines/<int:line_id>/request-price",
    methods=["POST"],
)
@login_required
def request_better_price(sub_id, line_id):
    """Customer submits request for a lower price on a subscription line."""
    requested_price_str = request.form.get("requested_price", "").strip()
    customer_note = request.form.get("customer_note", "").strip()

    try:
        req_price = _to_decimal(requested_price_str)
    except Exception:
        flash("Please enter a valid requested price.", "danger")
        return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))

    try:
        pr = submit_price_request(
            subscription_line_id=line_id,
            customer_id=current_user.id,
            requested_price=req_price,
            customer_note=customer_note,
        )
        flash(
            f"Price request for ₹{pr.requested_price} submitted! Our team will review your request.",
            "success",
        )
    except PriceRequestError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("subscriptions.subscription_detail", sub_id=sub_id))


@subscription_bp.route("/price-requests")
@login_required
def price_requests_list():
    """Staff views all price requests."""
    _require_staff_or_admin()
    requests = (
        PriceRequest.query
        .order_by(PriceRequest.created_at.desc())
        .all()
    )
    return render_template("price_requests.html", price_requests=requests)


@subscription_bp.route("/price-requests/<int:req_id>/approve", methods=["POST"])
@login_required
def price_request_approve(req_id):
    """Staff approves a price request, updating subscription line price."""
    _require_staff_or_admin()
    approved_price_str = request.form.get("approved_price", "").strip()
    staff_note = request.form.get("staff_note", "").strip()

    try:
        approved_price = _to_decimal(approved_price_str)
    except Exception:
        flash("Invalid approved price.", "danger")
        return redirect(url_for("subscriptions.price_requests_list"))

    try:
        pr = approve_price_request(
            price_request_id=req_id,
            approved_price=approved_price,
            staff_note=staff_note,
        )
        flash(
            f"Price request approved at ₹{pr.approved_price}. Subscription line updated!",
            "success",
        )
    except PriceRequestError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("subscriptions.price_requests_list"))


@subscription_bp.route("/price-requests/<int:req_id>/reject", methods=["POST"])
@login_required
def price_request_reject(req_id):
    """Staff rejects a customer's price request."""
    _require_staff_or_admin()
    staff_note = request.form.get("staff_note", "").strip()

    try:
        reject_price_request(
            price_request_id=req_id,
            staff_note=staff_note,
        )
        flash("Price request rejected.", "info")
    except PriceRequestError as exc:
        flash(str(exc), "danger")

    return redirect(url_for("subscriptions.price_requests_list"))


