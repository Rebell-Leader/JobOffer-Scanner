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
from services.checkpoint import (
    CHECKPOINT_STAGES,
    CheckpointPayload,
    get_store,
)
from utils.timing import timed_block


class JobAnalysisState(TypedDict, total=False):
    """State threaded through the analysis pipeline."""

    job_posting: str
    job_details: dict
    company_analysis: dict
    salary_analysis: dict
    resume_text: str
    resume_analysis: dict
    final_report: str
    verdict: dict
    error: str
    manual_inputs: Optional[dict]
    model: Optional[str]
    progress_callback: Optional[Callable]
    checkpoint_key: Optional[str]


# ---------------------------------------------------------------------------
# Checkpoint-aware wrappers around the individual stage agents
# ---------------------------------------------------------------------------

def _save_checkpoint(state: Dict, stage: str, value) -> None:
    key = state.get("checkpoint_key")
    if not key or value in (None, {}, ""):
        return
    get_store().set(key, stage, value)


def _restore(state: Dict) -> CheckpointPayload:
    key = state.get("checkpoint_key")
    return get_store().get(key) if key else CheckpointPayload()


def _analyze_job_with_checkpoint(state: Dict) -> Dict:
    ckpt = _restore(state)
    if ckpt.has("job_details"):
        state["job_details"] = ckpt.get("job_details")
        cb = state.get("progress_callback")
        if cb:
            cb("job", 25, "Restored from checkpoint")
        return state
    with timed_block("pipeline.stage", tags={"stage": "job"}):
        state = job_analyzer.analyze(state)
    if not state.get("error"):
        _save_checkpoint(state, "job_details", state.get("job_details"))
    return state


def _analyze_company_and_salary(state: Dict) -> Dict:
    """Run company + salary concurrently, honoring per-stage checkpoints.

    Either branch can independently consult / write the checkpoint — so a
    partial run where (e.g.) salary failed but company succeeded won't redo
    the company call on retry.
    """
    if state.get("error"):
        return state

    callback = state.get("progress_callback")
    if callback:
        callback("company", 50)

    ckpt = _restore(state)
    company_done = ckpt.has("company_analysis")
    salary_done = ckpt.has("salary_analysis")

    if company_done:
        state["company_analysis"] = ckpt.get("company_analysis")
    if salary_done:
        state["salary_analysis"] = ckpt.get("salary_analysis")

    if company_done and salary_done:
        return state

    worker_state = dict(state)
    worker_state["progress_callback"] = None

    def _run(stage_name, fn, s):
        with timed_block("pipeline.stage", tags={"stage": stage_name}):
            return fn(s)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {}
        if not company_done:
            futures["company"] = pool.submit(_run, "company", company_analyzer.analyze, dict(worker_state))
        if not salary_done:
            futures["salary"] = pool.submit(_run, "salary", salary_analyzer.analyze, dict(worker_state))
        results = {name: f.result() for name, f in futures.items()}

    # Surface the first error from either branch — but persist whichever
    # half succeeded so a retry doesn't redo it.
    error = next(
        (r.get("error") for r in results.values() if r.get("error")),
        "",
    )

    if "company" in results and not results["company"].get("error"):
        state["company_analysis"] = results["company"].get("company_analysis", {})
        _save_checkpoint(state, "company_analysis", state["company_analysis"])
    if "salary" in results and not results["salary"].get("error"):
        state["salary_analysis"] = results["salary"].get("salary_analysis", {})
        _save_checkpoint(state, "salary_analysis", state["salary_analysis"])

    if error:
        state["error"] = error

    if callback:
        callback("salary", 75)
    return state


def _analyze_resume_with_checkpoint(state: Dict) -> Dict:
    if state.get("error"):
        return state
    if not state.get("resume_text"):
        return state  # resume is optional — no checkpoint, no work
    ckpt = _restore(state)
    if ckpt.has("resume_analysis"):
        state["resume_analysis"] = ckpt.get("resume_analysis")
        return state
    with timed_block("pipeline.stage", tags={"stage": "resume"}):
        state = resume_analyzer.analyze(state)
    if not state.get("error") and state.get("resume_analysis"):
        _save_checkpoint(state, "resume_analysis", state["resume_analysis"])
    return state


def _generate_report_with_checkpoint(state: Dict) -> Dict:
    if state.get("error"):
        return state
    ckpt = _restore(state)
    if ckpt.has("verdict_and_report"):
        bundle = ckpt.get("verdict_and_report") or {}
        state["final_report"] = bundle.get("final_report", "")
        state["verdict"] = bundle.get("verdict", {})
        return state
    with timed_block("pipeline.stage", tags={"stage": "report"}):
        state = report_generator.generate(state)
    if not state.get("error"):
        _save_checkpoint(state, "verdict_and_report", {
            "final_report": state.get("final_report", ""),
            "verdict": state.get("verdict", {}),
        })
    return state


def create_analysis_graph():
    workflow = StateGraph(JobAnalysisState)
    workflow.add_node("analyze_job", _analyze_job_with_checkpoint)
    workflow.add_node("analyze_company_and_salary", _analyze_company_and_salary)
    workflow.add_node("analyze_resume", _analyze_resume_with_checkpoint)
    workflow.add_node("generate_report", _generate_report_with_checkpoint)

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
    checkpoint_key: Optional[str] = None,
) -> dict:
    """Run the analysis pipeline.

    ``checkpoint_key`` opt-in: when present, completed stages are read from
    (and new completions written to) the in-process checkpoint store. A retry
    of the same call with the same key only re-runs failed/missing stages.
    On a successful end-to-end run the caller should clear the checkpoint
    (the UI does this after rendering the result).
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
        "checkpoint_key": checkpoint_key,
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
