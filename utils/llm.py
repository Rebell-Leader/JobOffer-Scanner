from openai import OpenAI
from typing import Dict, Optional
import os
import json

# Initialize OpenAI client with Featherless API
# For demo purposes, we will use mock responses if the API key is not set.
# In a production environment, ensure FEATHERLESS_API_KEY is set.
client = OpenAI(
    base_url="https://api.featherless.ai/v1",
    api_key=os.getenv("FEATHERLESS_API_KEY"),
    timeout=30.0  # Add timeout to prevent hanging
)

def get_llm_client() -> OpenAI:
    """Get the OpenAI client instance."""
    return client

def get_completion(prompt: str, model: str = "deepseek-ai/DeepSeek-R1-0528") -> str:
    """
    Get completion from LLM with proper error handling.
    Falls back to mock responses when API keys are not available or in demo mode.
    """
    try:
        # Check if the API key is set for the Featherless client
        api_key = os.getenv("FEATHERLESS_API_KEY")

        if not api_key:
            print(f"No FEATHERLESS_API_KEY found - using mock response for model: {model}")
            return generate_mock_response(prompt, model)

        # In a real scenario, you would use the client here.
        # For demo purposes, we'll still fall back to mock responses to show functionality.
        print(f"Demo mode - using mock response for model: {model}")
        return generate_mock_response(prompt, model)

    except Exception as e:
        print(f"LLM API Error: {str(e)}")
        # Return a mock error message that explains the issue
        return generate_mock_response(prompt, model) # Fallback to mock on any exception

def generate_mock_response(prompt: str, model: str) -> str:
    """
    Generate realistic mock responses for demo purposes.
    This simulates actual API responses for different types of analysis.
    """
    prompt_lower = prompt.lower()

    # Job details extraction
    if "extract key information" in prompt_lower and "job posting" in prompt_lower:
        return """{
            "company_name": "TechCorp Inc.",
            "job_title": "Senior AI Engineer",
            "location": "San Francisco, CA (Remote Friendly)",
            "experience_level": "5+ years in ML/AI",
            "required_skills": ["Python", "TensorFlow", "PyTorch", "AWS", "Docker", "Kubernetes"],
            "compensation": "$140k - $180k + equity",
            "job_type": "Full-time",
            "responsibilities": [
                "Develop and deploy machine learning models",
                "Build scalable AI infrastructure",
                "Collaborate with cross-functional teams",
                "Mentor junior engineers"
            ]
        }"""

    # Requirements analysis
    elif "analyze the requirements" in prompt_lower:
        return """{
            "technical_skills": [
                "Python (Expert level required)",
                "Machine Learning Frameworks (TensorFlow/PyTorch)",
                "Cloud Platforms (AWS/GCP preferred)",
                "Containerization (Docker, Kubernetes)",
                "Version Control (Git)",
                "API Development (REST/GraphQL)"
            ],
            "soft_skills": [
                "Strong communication skills",
                "Team collaboration",
                "Problem-solving mindset",
                "Mentoring abilities"
            ],
            "education": "Bachelor's or Master's in Computer Science, AI, or related field",
            "experience": "5+ years in AI/ML with production deployment experience",
            "unique_requirements": [
                "Experience with real-time ML systems",
                "Previous startup experience preferred"
            ],
            "tools_and_technologies": [
                "Jupyter Notebooks", "MLflow", "Airflow", "Redis", "PostgreSQL"
            ]
        }"""

    # Company analysis
    elif "company" in prompt_lower and ("financial" in prompt_lower or "stability" in prompt_lower):
        return """## Company Financial Stability Analysis

**Overall Assessment: POSITIVE**

### Financial Health
- **Revenue Growth**: 25% year-over-year growth 
- **Funding Status**: Series B ($50M raised in 2023)
- **Burn Rate**: Healthy 18-month runway
- **Profitability**: Expected to reach profitability in Q4 2024

### Market Position  
- **Industry**: Growing AI/ML market segment
- **Competition**: Strong competitive position
- **Innovation**: Active R&D and patent portfolio

### Risk Factors
- **Market Dependency**: Some customer concentration risk
- **Regulatory**: Potential AI regulation impacts
- **Talent**: Competitive hiring market

**Recommendation**: STABLE - Good growth trajectory with solid funding."""

    # Salary analysis
    elif "salary" in prompt_lower or "compensation" in prompt_lower:
        return """## Compensation Analysis Report

### Market Benchmarking
- **Base Salary Range**: $140k - $180k
- **Market Percentile**: 70th percentile for role/location
- **Total Compensation**: $160k - $220k including equity

### Cost of Living Analysis
- **Location**: San Francisco, CA
- **Housing**: $3,500/month average rent
- **Effective Salary**: ~$120k after COL adjustment
- **Remote Option**: Significant value-add

### Package Strengths
- Competitive base salary
- Equity upside potential  
- Comprehensive benefits
- Remote work flexibility

### Negotiation Opportunities
- Stock options vesting schedule
- Signing bonus potential
- Professional development budget

**Overall Rating**: EXCELLENT package for current market conditions."""

    # Final report generation
    elif "comprehensive analysis" in prompt_lower or "recommendation" in prompt_lower:
        return """# Job Opportunity Analysis Report

## Executive Summary
This position at TechCorp Inc. presents a **HIGHLY RECOMMENDED** opportunity for an experienced AI Engineer.

## Key Highlights
✅ **Company Stability**: Strong financial position with recent funding  
✅ **Compensation**: Above-market package ($160k-$220k total comp)  
✅ **Growth Potential**: Rapidly expanding AI team  
✅ **Work-Life Balance**: Remote-friendly culture  
✅ **Technology Stack**: Modern, cutting-edge tools  

## Detailed Assessment

### Role Fit Analysis
- **Technical Match**: 95% - Excellent alignment with required skills
- **Experience Level**: Perfect match for 5+ years requirement  
- **Growth Opportunity**: High - Senior role with mentoring responsibilities

### Company Evaluation
- **Financial Health**: Stable with strong growth trajectory
- **Culture**: Positive reviews highlighting innovation and collaboration
- **Market Position**: Well-positioned in growing AI market

### Compensation Excellence
- **Market Competitiveness**: 70th percentile 
- **Total Package Value**: $180k-$220k estimated
- **Negotiation Potential**: Good leverage for improvements

## Recommendations

### Immediate Actions
1. **Apply promptly** - High-quality opportunity likely to move fast
2. **Prepare for technical interviews** focusing on ML system design
3. **Research team structure** and recent company achievements

### Interview Strategy  
- Emphasize production ML deployment experience
- Highlight mentoring and leadership examples
- Ask about AI ethics and responsible development practices

### Negotiation Focus
- Request equity details and vesting schedule
- Inquire about professional development budget
- Clarify remote work policy specifics

## Risk Mitigation
- **Single point of concern**: Rapid growth may mean changing priorities
- **Mitigation**: Ask about role stability and team roadmap

## Final Verdict
**STRONG RECOMMEND** - This opportunity offers excellent career advancement, competitive compensation, and alignment with current AI market trends.

**Confidence Level**: 9/10"""

    # Default response
    else:
        return f"""**Demo Response** - This is a simulated response for the prompt type detected. 

In the production version, this would connect to:
- Real company financial APIs
- Live salary databases  
- Current market research
- Actual company reviews

*Detected model: {model}*
*Prompt type: General analysis*

For a real implementation, please add your API keys to the environment variables."""