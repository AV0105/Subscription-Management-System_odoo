from functools import wraps
from flask import Flask, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from config import Config
from extensions import db, login_manager
from models import User, Product, Plan

def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated or current_user.role not in roles:
                flash("You do not have permission to access this page.", "danger")
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'login'
    login_manager.login_message_category = 'info'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # -----------------------------
    # BASE & AUTH ROUTES
    # -----------------------------
    @app.route('/')
    def index():
        return render_template('base.html')

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if request.method == 'POST':
            name = request.form.get('name')
            email = request.form.get('email')
            password = request.form.get('password')
            role = request.form.get('role', 'Customer')

            if User.query.filter_by(email=email).first():
                flash('Email address is already registered.', 'danger')
                return redirect(url_for('register'))

            user = User(name=name, email=email, role=role)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            flash('Registration successful! Please log in.', 'success')
            return redirect(url_for('login'))
        return render_template('register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'POST':
            email = request.form.get('email')
            password = request.form.get('password')
            user = User.query.filter_by(email=email).first()

            if user and user.check_password(password):
                login_user(user)
                flash('Logged in successfully.', 'success')
                return redirect(url_for('index'))
            
            flash('Invalid email or password.', 'danger')
        return render_template('login.html')

    @app.route('/logout')
    @login_required
    def logout():
        logout_user()
        flash('You have been logged out.', 'info')
        return redirect(url_for('login'))

    # -----------------------------
    # PRODUCT ROUTES
    # -----------------------------
    @app.route('/products')
    @login_required
    def list_products():
        products = Product.query.all()
        return render_template('products.html', products=products)

    @app.route('/products/new', methods=['GET', 'POST'])
    @login_required
    @role_required('Admin', 'Staff')
    def create_product():
        if request.method == 'POST':
            product = Product(
                name=request.form.get('name'),
                product_type=request.form.get('product_type'),
                sales_price=request.form.get('sales_price'),
                cost_price=request.form.get('cost_price'),
                notes=request.form.get('notes')
            )
            db.session.add(product)
            db.session.commit()
            flash('Product created successfully!', 'success')
            return redirect(url_for('list_products'))
        return render_template('product_form.html', product=None)

    @app.route('/products/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    @role_required('Admin', 'Staff')
    def edit_product(id):
        product = Product.query.get_or_404(id)
        if request.method == 'POST':
            product.name = request.form.get('name')
            product.product_type = request.form.get('product_type')
            product.sales_price = request.form.get('sales_price')
            product.cost_price = request.form.get('cost_price')
            product.notes = request.form.get('notes')
            db.session.commit()
            flash('Product updated successfully!', 'success')
            return redirect(url_for('list_products'))
        return render_template('product_form.html', product=product)

    # -----------------------------
    # PLAN ROUTES
    # -----------------------------
    @app.route('/plans')
    @login_required
    def list_plans():
        plans = Plan.query.all()
        return render_template('plans.html', plans=plans)

    @app.route('/plans/new', methods=['GET', 'POST'])
    @login_required
    @role_required('Admin', 'Staff')
    def create_plan():
        if request.method == 'POST':
            plan = Plan(
                name=request.form.get('name'),
                billing_frequency=request.form.get('billing_frequency'),
                minimum_quantity=request.form.get('minimum_quantity', 1),
                price=request.form.get('price'),
                start_date=request.form.get('start_date') or None,
                end_date=request.form.get('end_date') or None,
                auto_close='auto_close' in request.form,
                allow_renewal='allow_renewal' in request.form
            )
            db.session.add(plan)
            db.session.commit()
            flash('Plan created successfully!', 'success')
            return redirect(url_for('list_plans'))
        return render_template('plan_form.html', plan=None)

    @app.route('/plans/<int:id>/edit', methods=['GET', 'POST'])
    @login_required
    @role_required('Admin', 'Staff')
    def edit_plan(id):
        plan = Plan.query.get_or_404(id)
        if request.method == 'POST':
            plan.name = request.form.get('name')
            plan.billing_frequency = request.form.get('billing_frequency')
            plan.minimum_quantity = request.form.get('minimum_quantity')
            plan.price = request.form.get('price')
            plan.start_date = request.form.get('start_date') or None
            plan.end_date = request.form.get('end_date') or None
            plan.auto_close = 'auto_close' in request.form
            plan.allow_renewal = 'allow_renewal' in request.form
            db.session.commit()
            flash('Plan updated successfully!', 'success')
            return redirect(url_for('list_plans'))
        return render_template('plan_form.html', plan=plan)

    return app

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        db.create_all()
    app.run(debug=True)