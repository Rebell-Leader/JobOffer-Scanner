from typing import Callable, Dict, Optional, TypedDict

from langgraph.graph import StateGraph

from agents import company_analyzer, job_analyzer, report_generator, salary_analyzer


class JobAnalysisState(TypedDict, total=False):
    """State threaded through the analysis pipeline.

    ``total=False`` lets nodes set fields incrementally without listing every
    key on construction. The ``job_posting`` field used to be smuggled in via
    ``graph.invoke`` without being declared — it now lives in the schema.
    """

    job_posting: str
    job_details: dict
    company_analysis: dict
    salary_analysis: dict
    final_report: str
    error: str
    manual_inputs: Optional[dict]
    model: Optional[str]
    progress_callback: Optional[Callable]


def create_analysis_graph():
    workflow = StateGraph(JobAnalysisState)
    workflow.add_node("analyze_job", job_analyzer.analyze)
    workflow.add_node("analyze_company", company_analyzer.analyze)
    workflow.add_node("analyze_salary", salary_analyzer.analyze)
    workflow.add_node("generate_report", report_generator.generate)

    workflow.add_edge("analyze_job", "analyze_company")
    workflow.add_edge("analyze_company", "analyze_salary")
    workflow.add_edge("analyze_salary", "generate_report")

    workflow.set_entry_point("analyze_job")
    return workflow.compile()


def run_analysis(
    job_posting: str,
    manual_inputs: Optional[dict] = None,
    model: str = "detailed",
    progress_callback: Optional[Callable] = None,
) -> dict:
    """Run the four-stage analysis pipeline.

    ``model`` is a logical tier ("fast"/"detailed") or an explicit model id;
    the LLM layer resolves it for whichever provider is active.
    """
    graph = create_analysis_graph()

    initial_state: JobAnalysisState = {
        "job_posting": job_posting,
        "job_details": {},
        "company_analysis": {},
        "salary_analysis": {},
        "final_report": "",
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
