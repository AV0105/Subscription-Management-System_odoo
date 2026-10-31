"""
auth_routes.py
==============
Authentication routes — Person 1 owns this file.

This is a MINIMAL stub so the application runs before Person 1
commits their full implementation.

Person 1 should replace this entirely with proper login/logout,
password hashing, and optional registration.
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required
from werkzeug.security import check_password_hash

from extensions import db
from models import User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Minimal login — Person 1 will replace this."""
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            flash("Logged in successfully.", "success")
            return redirect(request.args.get("next") or url_for("subscriptions.subscriptions_list"))
        else:
            flash("Invalid email or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))

