from typing import Dict

from tools.job_tools import analyze_requirements, extract_job_details


def analyze(state: Dict) -> Dict:
    job_posting = state.get("job_posting", "")
    manual_inputs = state.get("manual_inputs") or {}
    model = state.get("model", "detailed")
    progress_callback = state.get("progress_callback")

    try:
        # Always extract from the posting, then let any non-empty manual field
        # override the extracted value. (Skipping extraction whenever a company
        # name was typed used to drop location, seniority, skills and
        # responsibilities from the report.) Copy: the extraction is cached.
        extracted = dict(extract_job_details(job_posting, model))
        if isinstance(manual_inputs, dict):
            for key, value in manual_inputs.items():
                if value.strip() if isinstance(value, str) else value:
                    extracted[key] = value

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
