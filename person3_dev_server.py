"""
DEV-ONLY runner for Person 3 (Payments and Dashboard).

Purpose:
    Run and test Person 3 modules independently while Person 1 and Person 2
    are still building their parts.

This is NOT the real application entry point.
Person 1 owns app.py. Do not replace app.py with this file.

It provides, for development only:
    - a temporary login/logout using the shared User model
    - a redirect from / to /dashboard
    - a health endpoint to confirm database wiring

Run from the subscription_app directory:

    python person3_dev_server.py

Run and seed demo data in one command:

    python person3_dev_server.py --seed

Then open:

    http://127.0.0.1:5001/login

Demo login created by the seed script:
    admin@brightloop.test / Demo@12345
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

# Load .env if python-dotenv is installed.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(CURRENT_DIR, '.env'))
except ImportError:
    pass

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template_string,
    request,
    url_for,
)
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from werkzeug.security import check_password_hash

try:
    from .extensions import db
except ImportError:
    from extensions import db

# Prefer Person 1's shared login manager if it exists.
try:
    from extensions import login_manager as shared_login_manager
except ImportError:
    shared_login_manager = None

try:
    from .models import User, Subscription, Invoice, Payment
except ImportError:
    from models import User, Subscription, Invoice, Payment

try:
    from .person3_routes import register_person3_routes
except ImportError:
    from person3_routes import register_person3_routes


DEV_LOGIN_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Person 3 Dev Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <div class="container" style="max-width: 460px; margin-top: 80px;">

        <div class="alert alert-warning small">
            Development-only login for Person 3 testing.
            Person 1 owns the real authentication module.
        </div>

        <div class="card shadow-sm">
            <div class="card-body">
                <h5 class="card-title mb-3">Sign in</h5>

                {% with messages = get_flashed_messages(with_categories=true) %}
                    {% for category, message in messages %}
                        <div class="alert alert-{{ 'danger' if category in ['danger', 'error'] else category }} py-2 small">
                            {{ message }}
                        </div>
                    {% endfor %}
                {% endwith %}

                <form method="post">
                    <div class="mb-3">
                        <label class="form-label" for="email">Email</label>
                        <input type="email" class="form-control" id="email" name="email" required autofocus>
                    </div>

                    <div class="mb-3">
                        <label class="form-label" for="password">Password</label>
                        <input type="password" class="form-control" id="password" name="password" required>
                    </div>

                    <button type="submit" class="btn btn-primary w-100">Login</button>
                </form>
            </div>
        </div>
    </div>
</body>
</html>
"""


def _build_database_uri():
    """
    Fallback database URI if Person 1's config.py is not available yet.
    """
    driver = os.environ.get('DB_DRIVER', 'mysql+pymysql')
    user = os.environ.get('DB_USER', 'root')
    password = os.environ.get('DB_PASSWORD', '')
    host = os.environ.get('DB_HOST', '127.0.0.1')
    port = os.environ.get('DB_PORT', '3306')
    name = os.environ.get('DB_NAME', 'subscription_db')

    return f'{driver}://{user}:{password}@{host}:{port}/{name}'


def create_dev_app():
    """
    Build a development-only Flask app wired to the shared models and config.
    """
    app = Flask(__name__)

    # Use Person 1's config if it exists.
    try:
        from config import Config
        app.config.from_object(Config)
    except ImportError:
        pass

    if not app.config.get('SQLALCHEMY_DATABASE_URI'):
        app.config['SQLALCHEMY_DATABASE_URI'] = _build_database_uri()

    if not app.config.get('SECRET_KEY'):
        app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-only-secret-change-me')

    app.config.setdefault('SQLALCHEMY_TRACK_MODIFICATIONS', False)

    db.init_app(app)

    # Reuse the shared login manager if Person 1 created one.
    if shared_login_manager is not None:
        login_manager = shared_login_manager

        if getattr(login_manager, 'app', None) is None:
            login_manager.init_app(app)
    else:
        login_manager = LoginManager()
        login_manager.init_app(app)

    login_manager.login_view = 'dev_login'
    login_manager.login_message = 'Please log in to continue.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # Register Person 3 blueprints.
    register_person3_routes(app)

    @app.route('/')
    def dev_index():
        return redirect(url_for('dashboard.dashboard'))

    @app.route('/login', methods=['GET', 'POST'])
    def dev_login():
        if current_user.is_authenticated:
            return redirect(url_for('dashboard.dashboard'))

        if request.method == 'POST':
            email = (request.form.get('email') or '').strip().lower()
            password = request.form.get('password') or ''

            user = User.query.filter(
                db.func.lower(User.email) == email
            ).first()

            if user and check_password_hash(user.password_hash, password):
                login_user(user)
                flash('Logged in successfully.', 'success')
                return redirect(url_for('dashboard.dashboard'))

            flash('Invalid email or password.', 'danger')

        return render_template_string(DEV_LOGIN_TEMPLATE)

    @app.route('/logout')
    @login_required
    def dev_logout():
        logout_user()
        flash('You have been logged out.', 'success')
        return redirect(url_for('dev_login'))

    @app.route('/person3/health')
    @login_required
    def person3_health():
        """
        Quick wiring check for integration debugging.
        """
        try:
            return jsonify({
                'status': 'ok',
                'database': app.config.get('SQLALCHEMY_DATABASE_URI', '').split('@')[-1],
                'users': User.query.count(),
                'subscriptions': Subscription.query.count(),
                'invoices': Invoice.query.count(),
                'payments': Payment.query.count(),
                'blueprints': sorted(app.blueprints.keys()),
            })
        except Exception as exc:
            return jsonify({
                'status': 'error',
                'message': str(exc),
            }), 500

    return app


if __name__ == '__main__':
    dev_app = create_dev_app()

    with dev_app.app_context():
        db.create_all()

        if '--seed' in sys.argv:
            try:
                from .seed_person3_demo import seed_person3_demo
            except ImportError:
                from seed_person3_demo import seed_person3_demo

            seed_person3_demo()

    dev_app.run(
        host='127.0.0.1',
        port=int(os.environ.get('PORT', 5001)),
        debug=True,
    )