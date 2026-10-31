from flask import Blueprint, render_template, request, abort
from flask_login import login_required, current_user
from functools import wraps
from models import User, Subscription, Invoice

support_bp = Blueprint('support', __name__)

def internal_staff_required(f):
    """Guards routes so only Admin and Staff can access them."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        role = str(current_user.role).lower()
        if role not in ['admin', 'staff']:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

@support_bp.route('/customers')
@login_required
@internal_staff_required
def customer_directory():
    """Staff can search and view all customer accounts."""
    search = request.args.get('search', '').strip()
    
    # Safe filtering that works whether Person 1 used Enums or Strings for roles
    all_users = User.query.all()
    customers = []
    for u in all_users:
        if str(u.role).lower() in ['customer', 'user']:
            if not search or search.lower() in u.name.lower() or search.lower() in u.email.lower():
                customers.append(u)
                
    return render_template('customers.html', customers=customers, search=search)

@support_bp.route('/customers/<int:user_id>')
@login_required
@internal_staff_required
def customer_profile(user_id):
    """Staff can view a specific customer's subscriptions and billing history."""
    customer = User.query.get_or_404(user_id)
    
    # Ensure staff is actually looking at a customer, not another admin
    if str(customer.role).lower() not in ['customer', 'user']:
        abort(404)
        
    subscriptions = Subscription.query.filter_by(customer_id=user_id).all()
    invoices = Invoice.query.filter_by(customer_id=user_id).order_by(Invoice.issue_date.desc()).all()
    
    return render_template('customer_profile.html', customer=customer, subscriptions=subscriptions, invoices=invoices)