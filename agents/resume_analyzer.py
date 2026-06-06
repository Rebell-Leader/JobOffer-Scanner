"""Resume / ATS analysis stage. Only runs when a resume was uploaded."""

from typing import Dict

from tools.resume_tools import analyze_resume


def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state

    resume_text = state.get("resume_text") or ""
    if not resume_text.strip():
        # Nothing to do — resume is optional.
        return state

    model = state.get("model", "detailed")
    progress_callback = state.get("progress_callback")
    if progress_callback:
        progress_callback("resume", 85)

    job_details = state.get("job_details") or {}
    requirements = job_details.get("requirements_analysis") or {}
    required_skills = []
    if isinstance(requirements, dict):
        required_skills = (
            requirements.get("technical_skills")
            or requirements.get("tools_and_technologies")
            or []
        )

    try:
        state["resume_analysis"] = analyze_resume(
            resume_text=resume_text,
            job_posting=state.get("job_posting", ""),
            required_skills=required_skills,
            model=model,
        )
        if progress_callback:
            score = state["resume_analysis"]["ats_score"]
            progress_callback("resume", 90, f"ATS match score: {score}/100")
    except Exception as exc:  # noqa: BLE001 - surfaced into pipeline state
        state["error"] = f"Resume analysis failed: {exc}"

    return state
