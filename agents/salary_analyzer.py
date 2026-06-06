from typing import Dict

from tools.salary_tools import estimate_salary_range


def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state

    model = state.get("model", "detailed")
    progress_callback = state.get("progress_callback")

    if progress_callback:
        progress_callback("salary", 75)

    extracted = (state.get("job_details") or {}).get("extracted_details") or {}
    manual = state.get("manual_inputs") or {}
    if not isinstance(manual, dict):
        manual = {}

    job_title = manual.get("job_title") or extracted.get("job_title") or "Software Engineer"
    location = manual.get("location") or extracted.get("location") or "United States"
    experience_level = (
        manual.get("experience_level") or extracted.get("experience_level") or ""
    )

    try:
        salary_report = estimate_salary_range(
            job_title=job_title,
            location=location,
            experience_level=experience_level,
            model=model,
        )
        state["salary_analysis"] = {"estimated_range": salary_report}
        if progress_callback:
            progress_callback("salary", 100, f"Estimated salary range for {job_title} in {location}")
    except Exception as exc:  # noqa: BLE001 - surfaced into pipeline state
        state["error"] = f"Salary analysis failed: {exc}"

    return state
