from typing import Dict

from tools.job_tools import analyze_requirements, extract_job_details


def analyze(state: Dict) -> Dict:
    job_posting = state.get("job_posting", "")
    manual_inputs = state.get("manual_inputs") or {}
    model = state.get("model", "detailed")
    progress_callback = state.get("progress_callback")

    try:
        # Use manual inputs as the source-of-truth for the basics when provided.
        # Still run requirements extraction on the description for skill detail.
        if isinstance(manual_inputs, dict) and manual_inputs.get("company_name"):
            extracted = dict(manual_inputs)
        else:
            extracted = extract_job_details(job_posting, model)

        requirements = analyze_requirements(job_posting, model)
        state["job_details"] = {
            "extracted_details": extracted,
            "requirements_analysis": requirements,
        }

        if progress_callback:
            tech_skills = requirements.get("technical_skills", []) if isinstance(requirements, dict) else []
            progress_callback("job", 25, f"Found {len(tech_skills)} technical skills")

    except Exception as exc:  # noqa: BLE001 - surfaced into pipeline state
        state["error"] = f"Job analysis failed: {exc}"

    return state
