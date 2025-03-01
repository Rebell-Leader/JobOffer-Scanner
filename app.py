import streamlit as st
import os
from flask import Flask, render_template, redirect, url_for, flash, session, request
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash

from models import db, User # Assuming models.py exists and defines db and User
from auth import auth_bp # Assuming auth.py exists and defines auth_bp
from main import main_bp # Assuming main.py exists and defines main_bp
from datetime import datetime, timedelta
import stripe

# Initialize Flask app
app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)

# Initialize login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message_category = 'info'

# User loader callback
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)

# Function to create admin user
def create_admin_user():
    # Check if admin user exists
    admin_exists = User.query.filter_by(email='admin@applywise.com').first()

    if not admin_exists:
        # Create admin user
        admin_user = User(
            email='admin@applywise.com',
            name='Admin',
            password=generate_password_hash('adminpassword'),
            is_admin=True,
            is_premium=True,
            weekly_analyses_count=0,
            last_analysis_reset=datetime.utcnow()
        )
        db.session.add(admin_user)
        db.session.commit()

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('500.html'), 500

# Initialize database and create admin user when the app starts
with app.app_context():
    # Create tables if they don't exist
    db.create_all()
    # Create admin user
    create_admin_user()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)