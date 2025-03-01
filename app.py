import streamlit as st
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, Report, db
from agents.orchestrator import run_analysis
import json
from werkzeug.security import generate_password_hash, check_password_hash

# Load environment variables
load_dotenv()

# Page config
st.set_page_config(
    page_title="ApplyWise - Job Analysis Platform",
    page_icon="💼",
    layout="wide"
)

# Database setup
DATABASE_URL = os.environ.get("DATABASE_URL")
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)

# Initialize database and create tables if they don't exist
db.metadata.create_all(engine)

# Initialize session state for user authentication
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'user_name' not in st.session_state:
    st.session_state.user_name = None
if 'is_premium' not in st.session_state:
    st.session_state.is_premium = False
if 'analyses_remaining' not in st.session_state:
    st.session_state.analyses_remaining = 0
if 'page' not in st.session_state:
    st.session_state.page = 'login'

# Function to create admin user if it doesn't exist
def create_admin_user():
    session = Session()
    admin_exists = session.query(User).filter_by(email='admin@applywise.com').first()

    if not admin_exists:
        admin_user = User(
            email='admin@applywise.com',
            name='Admin',
            password=generate_password_hash('adminpassword'),
            is_admin=True,
            is_premium=True,
            weekly_analyses_count=0,
            last_analysis_reset=datetime.utcnow()
        )
        session.add(admin_user)
        session.commit()
    session.close()

# Create admin user
create_admin_user()

# Authentication functions
def login(email, password):
    session = Session()
    user = session.query(User).filter_by(email=email).first()

    if user and check_password_hash(user.password, password):
        # Update weekly analysis count if needed
        week_ago = datetime.utcnow() - timedelta(days=7)
        if user.last_analysis_reset < week_ago:
            user.weekly_analyses_count = 0
            user.last_analysis_reset = datetime.utcnow()
            session.commit()

        # Set session state
        st.session_state.user_id = user.id
        st.session_state.user_name = user.name
        st.session_state.is_premium = user.is_premium

        # Calculate analyses remaining
        analyses_limit = 30 if user.is_premium else 3
        st.session_state.analyses_remaining = max(0, analyses_limit - user.weekly_analyses_count)

        session.close()
        return True

    session.close()
    return False

def register(name, email, password):
    session = Session()
    existing_user = session.query(User).filter_by(email=email).first()

    if existing_user:
        session.close()
        return False, "Email already registered"

    # Create a new user
    new_user = User(
        email=email,
        name=name,
        password=generate_password_hash(password),
        weekly_analyses_count=0,
        last_analysis_reset=datetime.utcnow()
    )

    session.add(new_user)
    session.commit()
    session.close()
    return True, "Registration successful"

def logout():
    st.session_state.user_id = None
    st.session_state.user_name = None
    st.session_state.is_premium = False
    st.session_state.analyses_remaining = 0
    st.session_state.page = 'login'

# Navigation
def nav_to(page):
    st.session_state.page = page

# Function to save a report to the database
def save_report(result, job_data, job_description):
    session = Session()
    user = session.query(User).filter_by(id=st.session_state.user_id).first()

    if not user:
        session.close()
        return False, "User not found"

    # Create new report
    new_report = Report(
        user_id=user.id,
        company_name=job_data.get("company_name"),
        job_title=job_data.get("job_title"),
        location=job_data.get("location"),
        job_details=result.get("job_details"),
        company_analysis=result.get("company_analysis"),
        salary_analysis=result.get("salary_analysis"),
        final_report=result.get("final_report"),
        job_posting=job_description
    )

    # Update user's analysis count
    user.weekly_analyses_count += 1

    # Update session state
    analyses_limit = 30 if user.is_premium else 3
    st.session_state.analyses_remaining = max(0, analyses_limit - user.weekly_analyses_count)

    session.add(new_report)
    session.commit()
    session.close()
    return True, new_report.id

# Landing page
def show_landing_page():
    st.image("static/img/logo.svg", width=200)
    st.title("ApplyWise")
    st.write("Take the guesswork out of job applications. ApplyWise offers a detailed evaluation of job descriptions, market conditions, and company stability, ensuring you make the best career moves.")

    col1, col2, col3 = st.columns([1, 1, 1])
    with col1:
        if st.button("Login", key="login_btn", use_container_width=True):
            nav_to('login')
    with col2:
        if st.button("Register", key="register_btn", use_container_width=True):
            nav_to('register')
    with col3:
        if st.button("Learn More", key="learn_more_btn", use_container_width=True):
            nav_to('learn_more')

    st.markdown("---")
    st.subheader("Key Features")

    feature1, feature2, feature3 = st.columns(3)
    with feature1:
        st.markdown("### 🔍 Deep Job Analysis")
        st.write("Extract key requirements, skills, and hidden expectations from job postings.")

    with feature2:
        st.markdown("### 🏢 Company Stability")
        st.write("Evaluate company health, recent news, and long-term prospects.")

    with feature3:
        st.markdown("### 💰 Salary Insights")
        st.write("Get accurate compensation ranges adjusted for your location.")

# Login page
def show_login_page():
    st.title("Login to ApplyWise")

    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        if login(email, password):
            st.success("Login successful!")
            nav_to('dashboard')
        else:
            st.error("Invalid email or password")

    st.markdown("---")
    if st.button("Don't have an account? Register"):
        nav_to('register')

    if st.button("Back to Home"):
        nav_to('landing')

# Register page
def show_register_page():
    st.title("Register for ApplyWise")

    name = st.text_input("Name")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    confirm_password = st.text_input("Confirm Password", type="password")

    if st.button("Register"):
        if not all([name, email, password, confirm_password]):
            st.error("Please fill in all fields")
        elif password != confirm_password:
            st.error("Passwords do not match")
        else:
            success, message = register(name, email, password)
            if success:
                st.success(message)
                st.info("Please login with your new account")
                nav_to('login')
            else:
                st.error(message)

    st.markdown("---")
    if st.button("Already have an account? Login"):
        nav_to('login')

    if st.button("Back to Home"):
        nav_to('landing')

# Dashboard page
def show_dashboard():
    st.title(f"Welcome, {st.session_state.user_name}!")

    # Usage stats
    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("Account Type", "Premium" if st.session_state.is_premium else "Free")

    with col2:
        st.metric("Analyses Remaining", st.session_state.analyses_remaining)

    with col3:
        if not st.session_state.is_premium:
            if st.button("Upgrade to Premium"):
                nav_to('upgrade')

    # Navigation buttons
    if st.button("New Analysis", key="new_analysis_btn", use_container_width=True):
        nav_to('analyze')

    # Fetch reports from the database
    session = Session()
    reports = session.query(Report).filter_by(user_id=st.session_state.user_id).order_by(Report.created_at.desc()).all()
    session.close()

    if reports:
        st.subheader("Your Job Analyses")

        # Convert reports to a list of dictionaries for display
        report_data = []
        for report in reports:
            report_data.append({
                "id": report.id,
                "date": report.created_at.strftime("%Y-%m-%d"),
                "company": report.company_name,
                "position": report.job_title,
                "location": report.location
            })

        # Display reports as a table
        df = pd.DataFrame(report_data)
        st.dataframe(df, use_container_width=True)

        # Selection for viewing a report
        report_ids = [r["id"] for r in report_data]
        report_labels = [f"{r['company']} - {r['position']}" for r in report_data]
        selected_report = st.selectbox("Select a report to view", options=report_ids, format_func=lambda x: report_labels[report_ids.index(x)])

        if st.button("View Selected Report"):
            st.session_state.selected_report_id = selected_report
            nav_to('view_report')
    else:
        st.info("You haven't analyzed any jobs yet. Start by creating a new analysis.")

# Analyze job page
def show_analyze_page():
    st.title("Analyze a Job Posting")

    # Check if user has remaining analyses
    if st.session_state.analyses_remaining <= 0:
        st.error("You have reached your weekly analysis limit. Please upgrade to premium for more analyses.")
        if st.button("Upgrade to Premium"):
            nav_to('upgrade')
        if st.button("Back to Dashboard"):
            nav_to('dashboard')
        return

    # Job analysis form
    with st.form("job_analysis_form"):
        # Model selection
        model_choice = st.radio(
            "Select analysis model:",
            ["Fast (Qwen2.5-72B)", "Detailed (DeepSeek-R1)"],
            index=0,
            help="Choose between faster results or more detailed analysis"
        )

        # Basic job details
        col1, col2 = st.columns(2)

        with col1:
            company_name = st.text_input(
                "Company Name*",
                help="Enter the company name"
            )

            job_title = st.text_input(
                "Job Title*",
                help="Enter the position title"
            )

        with col2:
            location = st.text_input(
                "Location*",
                help="Enter job location (city, country)"
            )

            compensation = st.text_input(
                "Compensation",
                help="Enter salary/compensation details"
            )

        # Full job description
        job_description = st.text_area(
            "Full Job Description*",
            height=300,
            help="Paste the complete job posting text here"
        )

        # Submit button
        analyze_submitted = st.form_submit_button("Analyze Job", type="primary")

    if analyze_submitted:
        if job_description and company_name and job_title and location:
            # Create a progress container
            progress_container = st.container()

            with progress_container:
                st.subheader("Analysis Progress")
                progress_bar = st.progress(0)
                status_text = st.empty()

                # Set initial status
                status_text.text("Starting analysis...")

                # Initialize containers for tool findings
                tool_findings = st.container()

            with st.spinner("Analyzing job posting..."):
                # Prepare input
                job_data = {
                    "company_name": company_name,
                    "job_title": job_title,
                    "location": location,
                    "compensation": compensation
                }

                # Set the model based on user selection
                selected_model = "deepseek-ai/DeepSeek-R1" if "Detailed" in model_choice else "Qwen/Qwen2.5-72B-Instruct"

                # Update progress - Job analysis stage
                status_text.text("Analyzing job requirements...")
                progress_bar.progress(25)

                # Run analysis with progress callbacks
                def update_progress(stage, progress, stage_info=None):
                    if stage == "job":
                        status_text.text("Extracting job requirements...")
                        progress_bar.progress(25)
                    elif stage == "company":
                        status_text.text("Researching company information...")
                        progress_bar.progress(50)
                        if stage_info:
                            with tool_findings:
                                st.info(f"**Company Research:** {stage_info}")
                    elif stage == "salary":
                        status_text.text("Analyzing compensation and cost of living...")
                        progress_bar.progress(75)
                        if stage_info:
                            with tool_findings:
                                st.info(f"**Salary Analysis:** {stage_info}")
                    elif stage == "report":
                        status_text.text("Generating final recommendation...")
                        progress_bar.progress(100)

                # Run analysis
                result = run_analysis(job_description, job_data, selected_model, progress_callback=update_progress)

                if result.get("error"):
                    st.error(f"Analysis failed: {result['error']}")
                else:
                    # Final progress update
                    status_text.text("Analysis completed!")
                    progress_bar.progress(100)

                    # Save the report to the database
                    success, report_id = save_report(result, job_data, job_description)

                    if success:
                        st.success("Analysis completed and saved!")

                        # Display results in expandable sections
                        with st.expander("🎯 Job Analysis", expanded=True):
                            st.write(result["job_details"])

                            # Display skills found
                            requirements = result["job_details"].get("requirements_analysis", {})
                            if requirements:
                                st.subheader("Key Skills Required")
                                tech_skills = requirements.get("technical_skills", [])
                                if tech_skills:
                                    st.write("Technical Skills:")
                                    for skill in tech_skills:
                                        st.write(f"- {skill}")

                        with st.expander("🏢 Company Research", expanded=True):
                            st.write(result["company_analysis"])

                        with st.expander("💰 Compensation Analysis", expanded=True):
                            st.write(result["salary_analysis"])

                        st.subheader("📑 Final Recommendation")
                        st.markdown(result["final_report"])

                        if st.button("Back to Dashboard"):
                            nav_to('dashboard')
                    else:
                        st.error("Failed to save the report. Please try again.")
        else:
            st.warning("Please fill in all required fields marked with *")

    if st.button("Cancel and Return to Dashboard"):
        nav_to('dashboard')

# View report page
def show_view_report():
    if 'selected_report_id' not in st.session_state:
        st.error("No report selected")
        if st.button("Back to Dashboard"):
            nav_to('dashboard')
        return

    # Fetch the report from the database
    session = Session()
    report = session.query(Report).filter_by(id=st.session_state.selected_report_id, user_id=st.session_state.user_id).first()
    session.close()

    if not report:
        st.error("Report not found")
        if st.button("Back to Dashboard"):
            nav_to('dashboard')
        return

    # Display report
    st.title(f"{report.job_title} at {report.company_name}")
    st.caption(f"Created on {report.created_at.strftime('%Y-%m-%d')}")

    # Basic info
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Company:** {report.company_name}")
        st.markdown(f"**Position:** {report.job_title}")
    with col2:
        st.markdown(f"**Location:** {report.location}")
        if report.job_details and report.job_details.get("extracted_details", {}).get("compensation"):
            st.markdown(f"**Compensation:** {report.job_details['extracted_details']['compensation']}")

    # Report tabs
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Final Report", "Job Details", "Company Analysis", "Salary Analysis", "Original Posting"])

    with tab1:
        if report.final_report:
            st.markdown(report.final_report)
        else:
            st.info("No final report available")

    with tab2:
        if report.job_details and report.job_details.get("requirements_analysis"):
            requirements = report.job_details["requirements_analysis"]

            if requirements.get("technical_skills"):
                st.subheader("Technical Skills")
                for skill in requirements["technical_skills"]:
                    st.write(f"- {skill}")

            if requirements.get("soft_skills"):
                st.subheader("Soft Skills")
                for skill in requirements["soft_skills"]:
                    st.write(f"- {skill}")

            if requirements.get("education"):
                st.subheader("Education Requirements")
                st.write(requirements["education"])

            if requirements.get("experience"):
                st.subheader("Experience Requirements")
                st.write(requirements["experience"])

            if requirements.get("tools_and_technologies"):
                st.subheader("Tools & Technologies")
                for tool in requirements["tools_and_technologies"]:
                    st.write(f"- {tool}")
        else:
            st.info("No detailed job analysis available")

    with tab3:
        if report.company_analysis:
            if report.company_analysis.get("stability_analysis"):
                st.subheader("Company Stability Analysis")
                st.markdown(report.company_analysis["stability_analysis"])

            if report.company_analysis.get("company_reviews"):
                st.subheader("Company Reviews & Culture")
                st.markdown(report.company_analysis["company_reviews"])
        else:
            st.info("No company analysis available")

    with tab4:
        if report.salary_analysis and report.salary_analysis.get("estimated_range"):
            st.markdown(report.salary_analysis["estimated_range"])
        else:
            st.info("No salary analysis available")

    with tab5:
        st.subheader("Original Job Posting")
        st.code(report.job_posting)

    if st.button("Back to Dashboard"):
        nav_to('dashboard')

# Upgrade page
def show_upgrade_page():
    st.title("Upgrade to Premium")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Free Plan")
        st.markdown("**$0 / month**")
        st.markdown("✅ 3 analyses per week")
        st.markdown("✅ Basic job insights")
        st.markdown("✅ Company stability analysis")
        st.markdown("✅ Salary range estimation")
        st.button("Current Plan", disabled=True)

    with col2:
        st.subheader("Premium Plan")
        st.markdown("**$9 / month**")
        st.markdown("✅ **30 analyses per week**")
        st.markdown("✅ Detailed job insights")
        st.markdown("✅ Enhanced company analysis")
        st.markdown("✅ Comprehensive salary data")
        st.markdown("✅ Priority support")

        if st.button("Upgrade Now"):
            # Here we would integrate with Stripe for payment
            # For now, simulate an upgrade
            session = Session()
            user = session.query(User).filter_by(id=st.session_state.user_id).first()
            if user:
                user.is_premium = True
                user.subscription_end_date = datetime.utcnow() + timedelta(days=30)
                session.commit()

                # Update session state
                st.session_state.is_premium = True
                st.session_state.analyses_remaining = 30 - user.weekly_analyses_count

                st.success("Upgraded to premium successfully!")
                st.info("Redirecting to dashboard...")
                nav_to('dashboard')
            else:
                st.error("Failed to upgrade. Please try again.")
            session.close()

    st.markdown("---")
    st.subheader("Why Upgrade?")
    st.markdown("""
    With our Premium plan, active job seekers can analyze up to 30 job postings per week, 
    giving you a comprehensive understanding of potential employers and positions. 
    Make informed decisions about your career with detailed insights on company stability, 
    competitive salary ranges, and more.
    """)

    if st.button("Back to Dashboard"):
        nav_to('dashboard')

# Learn more page
def show_learn_more():
    st.title("About ApplyWise")

    st.write("""
    ApplyWise is an AI-powered job analysis platform that helps job seekers make informed career decisions.

    Our platform combines intelligent data extraction, comprehensive company research, and salary analysis
    to provide you with a complete picture of potential job opportunities.
    """)

    st.subheader("How It Works")

    st.markdown("""
    1. **Paste a job posting** - Copy and paste any job description you're interested in
    2. **AI analysis** - Our AI analyzes the requirements, company stability, and market conditions
    3. **Get insights** - Receive a comprehensive report with actionable recommendations
    """)

    st.subheader("Our Features")

    feature1, feature2, feature3 = st.columns(3)

    with feature1:
        st.markdown("### Job Requirements Analysis")
        st.markdown("""
        - Extract key technical skills
        - Identify soft skills and culture fit
        - Uncover hidden requirements
        - Analyze experience levels needed
        """)

    with feature2:
        st.markdown("### Company Stability Research")
        st.markdown("""
        - Recent company news and events
        - Financial stability indicators
        - Industry position and outlook
        - Growth or contraction signs
        """)

    with feature3:
        st.markdown("### Compensation Analysis")
        st.markdown("""
        - Market salary ranges
        - Cost of living adjustments
        - Benefits evaluation
        - Negotiation recommendations
        """)

    if st.button("Return to Home"):
        nav_to('landing')

# Main app logic
import pandas as pd

# Routing based on session state
if st.session_state.user_id is None:
    # Unauthenticated pages
    if st.session_state.page == 'login':
        show_login_page()
    elif st.session_state.page == 'register':
        show_register_page()
    elif st.session_state.page == 'learn_more':
        show_learn_more()
    else:
        show_landing_page()
else:
    # Authenticated pages
    if st.session_state.page == 'dashboard':
        show_dashboard()
    elif st.session_state.page == 'analyze':
        show_analyze_page()
    elif st.session_state.page == 'view_report':
        show_view_report()
    elif st.session_state.page == 'upgrade':
        show_upgrade_page()
    else:
        show_dashboard()

# Small footer with logout option
st.markdown("---")
col1, col2, col3 = st.columns([2, 1, 1])
with col3:
    if st.session_state.user_id is not None:
        if st.button("Logout"):
            logout()