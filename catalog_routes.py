from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from extensions import db
from models import Product, Plan
from decimal import Decimal

catalog_bp = Blueprint('catalog', __name__)

from flask_login import current_user
from functools import wraps
from flask import abort

def admin_or_staff_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Safely get role as lowercase string (handles Enums or Strings)
        role = str(current_user.role).lower()
        if role not in ['admin', 'staff']:
            abort(403)  # Blocks the user immediately
        return f(*args, **kwargs)
    return decorated_function

# --- PRODUCTS ---
@catalog_bp.route('/products')
@login_required
def products_list():
    products = Product.query.all()
    return render_template('products.html', products=products)

@catalog_bp.route('/products/new', methods=['POST'])
@login_required
@admin_or_staff_required
def product_create():
    try:
        name = request.form.get('name')
        product_type = request.form.get('product_type', 'Service')
        sales_price = Decimal(request.form.get('sales_price', '0'))
        cost_price = Decimal(request.form.get('cost_price', '0'))
        notes = request.form.get('notes')
        
        p = Product(name=name, product_type=product_type, sales_price=sales_price, cost_price=cost_price, notes=notes)
        db.session.add(p)
        db.session.commit()
        flash('Product created successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating product: {str(e)}', 'danger')
    return redirect(url_for('catalog.products_list'))

# --- PLANS ---
@catalog_bp.route('/plans')
@login_required
def plans_list():
    plans = Plan.query.all()
    return render_template('plans.html', plans=plans)

@catalog_bp.route('/plans/new', methods=['POST'])
@login_required
@admin_or_staff_required
def plan_create():
    try:
        name = request.form.get('name')
        billing_frequency = request.form.get('billing_frequency', 'Monthly')
        minimum_quantity = int(request.form.get('minimum_quantity', '1'))
        price = Decimal(request.form.get('price', '0'))
        
        p = Plan(name=name, billing_frequency=billing_frequency, minimum_quantity=minimum_quantity, price=price)
        db.session.add(p)
        db.session.commit()
        flash('Plan created successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating plan: {str(e)}', 'danger')
    return redirect(url_for('catalog.plans_list'))