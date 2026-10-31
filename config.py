"""
config.py
=========
Application configuration.

Person 1 owns/adjusts this file.
Values come from the .env file — never hard-code credentials here.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # reads .env from the project root


class Config:
    """Base configuration shared by all environments."""

    # -----------------------------------------------------------------
    # Flask core
    # -----------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-fallback-change-in-production")
    FLASK_ENV = os.environ.get("FLASK_ENV", "development")

    # -----------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------
    # If DATABASE_URL is defined in .env (e.g. MySQL for production/Person 1),
    # use it. Otherwise, default to local SQLite so developers can run and test
    # immediately without needing an active MySQL service running.
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root@127.0.0.1:3306/subscription_db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle": 280,          # recycle connections before MySQL wait_timeout
        "pool_pre_ping": True,        # verify connection health before using from pool
    }

    # -----------------------------------------------------------------
    # Invoice / subscription settings (Person 2 reads these)
    # -----------------------------------------------------------------
    # Number of days after issue_date before invoice becomes due
    DEFAULT_PAYMENT_TERMS_DAYS = int(os.environ.get("DEFAULT_PAYMENT_TERMS_DAYS", 30))

    # Simple flat tax rate (e.g. 0.18 = 18 %).
    # Person 1 can replace with a proper tax-rule table later;
    # Person 2's calculate_tax() will check for that first.
    DEFAULT_TAX_RATE = float(os.environ.get("DEFAULT_TAX_RATE", 0.18))


class DevelopmentConfig(Config):
    DEBUG = True


class TestingConfig(Config):
    TESTING = True
    # Use SQLite in-memory for tests so no MySQL instance is needed
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    DEFAULT_TAX_RATE = 0.18
    DEFAULT_PAYMENT_TERMS_DAYS = 30


class ProductionConfig(Config):
    DEBUG = False


# Map name → class; app.py picks the right one via FLASK_ENV
config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}

