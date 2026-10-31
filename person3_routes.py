"""
Person 3 route registration helper.

This module registers the Person 3 blueprints:
- payment_bp
- dashboard_bp

Usage in app.py or app factory:

    from person3_routes import register_person3_routes
    register_person3_routes(app)

If Person 1 already registers these blueprints manually, this file can be skipped.
"""

try:
    from .payment_routes import payment_bp
    from .dashboard_routes import dashboard_bp
except ImportError:
    from payment_routes import payment_bp
    from dashboard_routes import dashboard_bp


def register_person3_routes(app):
    """
    Register Person 3 blueprints with the main Flask application.

    This function avoids duplicate blueprint registration if it is called
    more than once during development or testing.
    """

    if 'payments' not in app.blueprints:
        app.register_blueprint(payment_bp)

    if 'dashboard' not in app.blueprints:
        app.register_blueprint(dashboard_bp)

    return app