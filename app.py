"""
app.py
======
Flask application factory.

Person 1 owns this file.
This stub is provided by Person 2 so the application can run and be
tested while Person 1 builds out auth, products, and plans.

Person 1 should add:
  - auth_routes blueprint (login / logout / register)
  - catalog_routes blueprint (products / plans CRUD)
  - login_manager.user_loader callback
  - seed/demo data command
"""

import os
from flask import Flask, redirect, url_for
from dotenv import load_dotenv

from config import config_by_name
from extensions import db, login_manager, migrate

load_dotenv()


def create_app(config_name: str = None) -> Flask:
    """
    Application factory.

    Args:
        config_name: one of 'development', 'testing', 'production'.
                     Falls back to FLASK_ENV env var, then 'development'.
    """
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(config_by_name[config_name])

    # ── Initialise extensions ──────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    # ── Flask-Login: load user from session ───────────────────────
    # Person 1: move this into auth_routes.py or keep here — either works.
    from models import User

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # ── Register blueprints ───────────────────────────────────────
    # Person 2's blueprint
    from subscription_routes import subscription_bp
    app.register_blueprint(subscription_bp)

    # Person 1 will replace/expand auth_routes.py
    from auth_routes import auth_bp
    app.register_blueprint(auth_bp)
    from person3_routes import register_person3_routes
    register_person3_routes(app)
    # Person 1 adds catalog_routes when ready:
    # from catalog_routes import catalog_bp
    # app.register_blueprint(catalog_bp)

    # Person 3 adds:
    # from payment_routes import payment_bp
    # app.register_blueprint(payment_bp)
    from person3_routes import register_person3_routes
    register_person3_routes(app)

    # ── Root redirect ──────────────────────────────────────────────
    @app.route("/")
    def index():
        return redirect(url_for("subscriptions.subscriptions_list"))

    # ── CLI: create all tables ─────────────────────────────────────
    @app.cli.command("init-db")
    def init_db():
        """Create all database tables."""
        db.create_all()
        print("Database tables created.")

    # ── CLI: seed demo data ───────────────────────────────────────
    @app.cli.command("seed-db")
    def seed_db():
        """Seed demo users, products, plans for testing."""
        _seed_demo_data(app)

    return app


def _seed_demo_data(app):
    """
    Insert minimal demo data so the app is usable immediately after setup.
    Safe to call multiple times — skips if data already exists.
    """
    from werkzeug.security import generate_password_hash
    from models import User, Product, Plan, Role

    with app.app_context():
        # Admin user
        if not User.query.filter_by(email="admin@brightloop.com").first():
            admin = User(
                name="Admin User",
                email="admin@brightloop.com",
                password_hash=generate_password_hash("admin123"),
                role=Role.ADMIN,
            )
            db.session.add(admin)

        # Customer user
        if not User.query.filter_by(email="alice@example.com").first():
            alice = User(
                name="Alice Johnson",
                email="alice@example.com",
                password_hash=generate_password_hash("alice123"),
                role=Role.CUSTOMER,
            )
            db.session.add(alice)

        # Product
        if not Product.query.filter_by(name="Software License").first():
            product = Product(
                name="Software License",
                product_type="Service",
                sales_price=100.00,
                cost_price=20.00,
                notes="Annual software subscription license",
            )
            db.session.add(product)

        # Plan
        if not Plan.query.filter_by(name="Monthly Plan").first():
            plan = Plan(
                name="Monthly Plan",
                billing_frequency="Monthly",
                price=99.00,
                min_quantity=1,
            )
            db.session.add(plan)

        db.session.commit()
        print("Demo data seeded successfully.")


# ── Dev-server entry point ─────────────────────────────────────────
if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        # Automatically ensure database tables & demo data exist on startup
        db.create_all()
        _seed_demo_data(app)
    app.run(debug=True, host="0.0.0.0", port=5000)
