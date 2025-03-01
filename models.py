from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)
    is_premium = db.Column(db.Boolean, default=False)
    subscription_end_date = db.Column(db.DateTime, nullable=True)
    stripe_customer_id = db.Column(db.String(100), nullable=True)
    weekly_analyses_count = db.Column(db.Integer, default=0)
    last_analysis_reset = db.Column(db.DateTime, default=datetime.utcnow)

    reports = db.relationship('Report', backref='user', lazy=True)

    def __repr__(self):
        return f'<User {self.email}>'

    def reset_weekly_count_if_needed(self):
        """Check if we need to reset the weekly analysis counter"""
        week_ago = datetime.utcnow() - timedelta(days=7)
        if self.last_analysis_reset < week_ago:
            self.weekly_analyses_count = 0
            self.last_analysis_reset = datetime.utcnow()
            return True
        return False
    
    @property
    def analyses_limit(self):
        """Get the weekly analysis limit based on account type"""
        return 30 if self.is_premium else 3
    
    @property
    def analyses_remaining(self):
        """Get the number of analyses remaining this week"""
        return max(0, self.analyses_limit - self.weekly_analyses_count)
    
    def has_available_analyses(self):
        """Check if the user has analyses remaining"""
        self.reset_weekly_count_if_needed()
        return self.weekly_analyses_count < self.analyses_limit

class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Job details
    company_name = db.Column(db.String(100), nullable=False)
    job_title = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100))
    
    # Analysis results (stored as JSON)
    job_details = db.Column(db.JSON)
    company_analysis = db.Column(db.JSON)
    salary_analysis = db.Column(db.JSON)
    final_report = db.Column(db.Text)
    
    # Original job posting
    job_posting = db.Column(db.Text)

    def __repr__(self):
        return f'<Report {self.id}: {self.job_title} at {self.company_name}>'
