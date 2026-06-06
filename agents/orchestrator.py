from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional, TypedDict

from langgraph.graph import StateGraph

from agents import (
    company_analyzer,
    job_analyzer,
    report_generator,
    resume_analyzer,
    salary_analyzer,
)


class JobAnalysisState(TypedDict, total=False):
    """State threaded through the analysis pipeline."""

    job_posting: str
    job_details: dict
    company_analysis: dict
    salary_analysis: dict
    resume_text: str          # raw resume text (optional)
    resume_analysis: dict     # ATS / gap analysis output
    final_report: str
    verdict: dict             # structured Green/Yellow/Red verdict
    error: str
    manual_inputs: Optional[dict]
    model: Optional[str]
    progress_callback: Optional[Callable]


def _analyze_company_and_salary(state: Dict) -> Dict:
    """Run company + salary analysis concurrently.

    They depend only on the job stage, not each other. Progress callbacks fire
    from THIS (main) thread; worker copies have their callback disabled to
    avoid cross-thread UI updates.
    """
    if state.get("error"):
        return state

    callback = state.get("progress_callback")
    if callback:
        callback("company", 50)

    worker_state = dict(state)
    worker_state["progress_callback"] = None

    with ThreadPoolExecutor(max_workers=2) as pool:
        company_future = pool.submit(company_analyzer.analyze, dict(worker_state))
        salary_future = pool.submit(salary_analyzer.analyze, dict(worker_state))
        company_result = company_future.result()
        salary_result = salary_future.result()

    error = company_result.get("error") or salary_result.get("error")
    if error:
        state["error"] = error

    state["company_analysis"] = company_result.get("company_analysis", {})
    state["salary_analysis"] = salary_result.get("salary_analysis", {})

    if callback:
        callback("salary", 75)
    return state


def create_analysis_graph():
    workflow = StateGraph(JobAnalysisState)
    workflow.add_node("analyze_job", job_analyzer.analyze)
    workflow.add_node("analyze_company_and_salary", _analyze_company_and_salary)
    workflow.add_node("analyze_resume", resume_analyzer.analyze)
    workflow.add_node("generate_report", report_generator.generate)

    workflow.add_edge("analyze_job", "analyze_company_and_salary")
    workflow.add_edge("analyze_company_and_salary", "analyze_resume")
    workflow.add_edge("analyze_resume", "generate_report")

    workflow.set_entry_point("analyze_job")
    return workflow.compile()


def run_analysis(
    job_posting: str,
    manual_inputs: Optional[dict] = None,
    model: str = "detailed",
    progress_callback: Optional[Callable] = None,
    resume_text: Optional[str] = None,
) -> dict:
    """Run the analysis pipeline.

    ``model`` is a logical tier ("fast"/"detailed") or an explicit model id;
    the LLM layer resolves it for whichever provider is active.
    ``resume_text`` is optional — when present, the resume/ATS stage runs.
    """
    graph = create_analysis_graph()

    initial_state: JobAnalysisState = {
        "job_posting": job_posting,
        "job_details": {},
        "company_analysis": {},
        "salary_analysis": {},
        "resume_text": resume_text or "",
        "resume_analysis": {},
        "final_report": "",
        "verdict": {},
        "error": "",
        "manual_inputs": manual_inputs,
        "model": model,
        "progress_callback": progress_callback,
    }

    if progress_callback:
        progress_callback("job", 25)

    try:
        return graph.invoke(initial_state)
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller
        return {
            **initial_state,
            "progress_callback": None,
            "error": f"Pipeline failed: {exc}",
        }
