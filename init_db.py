import os
from flask import Flask
from flask_migrate import Migrate, upgrade
from models import db, User
from werkzeug.security import generate_password_hash
from datetime import datetime

def create_app():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    migrate = Migrate(app, db)
    return app, migrate

def init_db():
    app, migrate = create_app()
    
    with app.app_context():
        # Run migrations
        upgrade()
        
        # Check if admin user exists
        admin = User.query.filter_by(email='admin@applywise.com').first()
        if not admin:
            # Create admin user
            admin = User(
                email='admin@applywise.com',
                name='Admin',
                password=generate_password_hash('adminpassword'),
                is_admin=True,
                is_premium=True,
                weekly_analyses_count=0,
                last_analysis_reset=datetime.utcnow()
            )
            db.session.add(admin)
            db.session.commit()
            print("Admin user created successfully.")
        else:
            print("Admin user already exists.")
        
        print("Database initialized successfully.")

if __name__ == '__main__':
    init_db()
