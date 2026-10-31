"""
extensions.py
=============
Shared Flask extension instances.

Person 1 owns this file — initialise all extensions here
so that circular-import problems are avoided.

Person 2 / Person 3 import `db`, `login_manager`, `migrate`
from here — do NOT create new SQLAlchemy instances elsewhere.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate

# Single shared SQLAlchemy instance — used across ALL models
db = SQLAlchemy()

# Flask-Login manager — Person 1 wires up user_loader in auth
login_manager = LoginManager()
login_manager.login_view = "auth.login"          # redirect target when @login_required fails
login_manager.login_message_category = "warning"

# Flask-Migrate for Alembic database migrations
migrate = Migrate()

