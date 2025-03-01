from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timedelta
from models import db, User, Report
from agents.orchestrator import run_analysis
import json

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    # Landing page
    return render_template('main/index.html')

@main_bp.route('/dashboard')
@login_required
def dashboard():
    # Get user's reports
    reports = Report.query.filter_by(user_id=current_user.id).order_by(Report.created_at.desc()).all()
    
    # Calculate analyses remaining this week
    analyses_limit = 30 if current_user.is_premium else 3
    analyses_used = current_user.weekly_analyses_count
    analyses_remaining = max(0, analyses_limit - analyses_used)
    
    # Check if we need to reset the weekly counter
    week_ago = datetime.utcnow() - timedelta(days=7)
    if current_user.last_analysis_reset < week_ago:
        current_user.weekly_analyses_count = 0
        current_user.last_analysis_reset = datetime.utcnow()
        db.session.commit()
        analyses_used = 0
        analyses_remaining = analyses_limit
    
    return render_template(
        'main/dashboard.html', 
        reports=reports, 
        analyses_used=analyses_used,
        analyses_remaining=analyses_remaining,
        is_premium=current_user.is_premium
    )

@main_bp.route('/analyze', methods=['GET', 'POST'])
@login_required
def analyze():
    if request.method == 'POST':
        # Check if user has available analyses
        analyses_limit = 30 if current_user.is_premium else 3
        if current_user.weekly_analyses_count >= analyses_limit:
            flash('You have reached your weekly analysis limit. Please upgrade to premium for more analyses.', 'warning')
            return redirect(url_for('auth.upgrade'))
        
        # Get form data
        job_description = request.form.get('job_description')
        company_name = request.form.get('company_name')
        job_title = request.form.get('job_title')
        location = request.form.get('location')
        compensation = request.form.get('compensation')
        model_choice = request.form.get('model_choice', 'Fast (Qwen2.5-72B)')
        
        if not all([job_description, company_name, job_title, location]):
            flash('Please fill in all required fields.', 'danger')
            return redirect(url_for('main.analyze'))
        
        # Prepare job data
        job_data = {
            "company_name": company_name,
            "job_title": job_title,
            "location": location,
            "compensation": compensation
        }
        
        # Set the model based on user selection
        selected_model = "deepseek-ai/DeepSeek-R1" if "Detailed" in model_choice else "Qwen/Qwen2.5-72B-Instruct"
        
        # Run analysis
        result = run_analysis(job_description, job_data, selected_model)
        
        if result.get("error"):
            flash(f'Analysis failed: {result["error"]}', 'danger')
            return redirect(url_for('main.analyze'))
        
        # Create new report in database
        new_report = Report(
            user_id=current_user.id,
            company_name=company_name,
            job_title=job_title,
            location=location,
            job_details=result.get("job_details"),
            company_analysis=result.get("company_analysis"),
            salary_analysis=result.get("salary_analysis"),
            final_report=result.get("final_report"),
            job_posting=job_description
        )
        
        # Update user's analysis count
        current_user.weekly_analyses_count += 1
        
        # Commit to database
        db.session.add(new_report)
        db.session.commit()
        
        # Redirect to the report view
        flash('Analysis completed successfully!', 'success')
        return redirect(url_for('main.view_report', report_id=new_report.id))
    
    # Get request - show the analysis form
    return render_template('main/analyze.html')

@main_bp.route('/report/<int:report_id>')
@login_required
def view_report(report_id):
    # Get the report
    report = Report.query.get_or_404(report_id)
    
    # Make sure the report belongs to the current user
    if report.user_id != current_user.id:
        flash('Access denied.', 'danger')
        return redirect(url_for('main.dashboard'))
    
    return render_template('main/report.html', report=report)

@main_bp.route('/api/reports')
@login_required
def api_reports():
    # API endpoint for getting reports (for AJAX loading)
    reports = Report.query.filter_by(user_id=current_user.id).order_by(Report.created_at.desc()).all()
    return jsonify([{
        'id': r.id,
        'company_name': r.company_name,
        'job_title': r.job_title,
        'location': r.location,
        'created_at': r.created_at.strftime('%Y-%m-%d %H:%M')
    } for r in reports])
