from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User
from auth.forms import LoginForm, RegisterForm
from datetime import datetime, timedelta
import stripe
import os

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    # Redirect if user is already logged in
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data).first()
        
        # Check if user exists and password is correct
        if user and check_password_hash(user.password, form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('main.dashboard'))
        else:
            flash('Login failed. Please check your email and password.', 'danger')
    
    return render_template('auth/login.html', form=form)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    # Redirect if user is already logged in
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    
    form = RegisterForm()
    if form.validate_on_submit():
        # Check if email already exists
        existing_user = User.query.filter_by(email=form.email.data).first()
        if existing_user:
            flash('Email already registered. Please login instead.', 'warning')
            return redirect(url_for('auth.login'))
        
        # Create a new user
        new_user = User(
            email=form.email.data,
            name=form.name.data,
            password=generate_password_hash(form.password.data),
            weekly_analyses_count=0,
            last_analysis_reset=datetime.utcnow()
        )
        
        # Optionally create a Stripe customer
        if os.environ.get('STRIPE_API_KEY'):
            stripe.api_key = os.environ.get('STRIPE_API_KEY')
            try:
                customer = stripe.Customer.create(
                    email=form.email.data,
                    name=form.name.data,
                    metadata={"user_id": str(new_user.id)}
                )
                new_user.stripe_customer_id = customer.id
            except Exception as e:
                current_app.logger.error(f"Stripe customer creation failed: {str(e)}")
        
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/register.html', form=form)

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('main.index'))

# Route for upgrading to premium
@auth_bp.route('/upgrade', methods=['GET'])
@login_required
def upgrade():
    if current_user.is_premium:
        flash('You already have a premium subscription!', 'info')
        return redirect(url_for('main.dashboard'))
    
    return render_template('auth/upgrade.html')

# Route for handling Stripe checkout
@auth_bp.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout_session():
    if os.environ.get('STRIPE_API_KEY'):
        stripe.api_key = os.environ.get('STRIPE_API_KEY')
        
        try:
            # Create a checkout session
            checkout_session = stripe.checkout.Session.create(
                customer=current_user.stripe_customer_id,
                payment_method_types=['card'],
                line_items=[{
                    'price': os.environ.get('STRIPE_PRICE_ID'),  # Monthly subscription price ID from Stripe
                    'quantity': 1,
                }],
                mode='subscription',
                success_url=url_for('auth.checkout_success', _external=True),
                cancel_url=url_for('auth.checkout_cancel', _external=True),
            )
            return redirect(checkout_session.url, code=303)
        except Exception as e:
            current_app.logger.error(f"Stripe checkout failed: {str(e)}")
            flash('Payment processing failed. Please try again later.', 'danger')
            return redirect(url_for('auth.upgrade'))
    else:
        flash('Payment system is currently unavailable.', 'warning')
        return redirect(url_for('main.dashboard'))

@auth_bp.route('/checkout-success')
@login_required
def checkout_success():
    # Update user to premium
    current_user.is_premium = True
    current_user.subscription_end_date = datetime.utcnow() + timedelta(days=30)
    db.session.commit()
    
    flash('Thank you for upgrading to premium! You now have access to 30 analyses per week.', 'success')
    return redirect(url_for('main.dashboard'))

@auth_bp.route('/checkout-cancel')
@login_required
def checkout_cancel():
    flash('Your payment was cancelled.', 'info')
    return redirect(url_for('auth.upgrade'))
