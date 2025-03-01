from langgraph.graph import Graph, StateGraph
from typing import Dict, TypedDict, Optional
from utils.llm import get_completion
from agents import job_analyzer, company_analyzer, salary_analyzer, report_generator

class JobAnalysisState(TypedDict):
    job_details: dict
    company_analysis: dict
    salary_analysis: dict
    final_report: str
    error: str
    manual_inputs: Optional[dict]
    model: Optional[str]

def create_analysis_graph():
    workflow = StateGraph(JobAnalysisState)

    # Add nodes
    workflow.add_node("analyze_job", job_analyzer.analyze)
    workflow.add_node("analyze_company", company_analyzer.analyze)
    workflow.add_node("analyze_salary", salary_analyzer.analyze)
    workflow.add_node("generate_report", report_generator.generate)

    # Define edges
    workflow.add_edge("analyze_job", "analyze_company")
    workflow.add_edge("analyze_company", "analyze_salary")
    workflow.add_edge("analyze_salary", "generate_report")

    # Set entry point
    workflow.set_entry_point("analyze_job")

    return workflow.compile()

def run_analysis(job_posting: str, manual_inputs: Optional[dict] = None, model: str = "deepseek-ai/DeepSeek-R1") -> dict:
    graph = create_analysis_graph()

    initial_state = JobAnalysisState(
        job_details={},
        company_analysis={},
        salary_analysis={},
        final_report="",
        error="",
        manual_inputs=manual_inputs,
        model=model
    )

    try:
        result = graph.invoke({
            "job_posting": job_posting,
            **initial_state
        })
        return result
    except Exception as e:
        return JobAnalysisState(
            job_details={},
            company_analysis={},
            salary_analysis={},
            final_report="",
            error=str(e),
            manual_inputs=manual_inputs,
            model=model
        )