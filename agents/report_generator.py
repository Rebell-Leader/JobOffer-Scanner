import json
from typing import Dict

from utils.llm import get_completion


def generate(state: Dict) -> Dict:
    if state.get("error"):
        return state

    model = state.get("model", "detailed")
    progress_callback = state.get("progress_callback")

    if progress_callback:
        progress_callback("report", 90)

    job_details = state.get("job_details") or {}
    extracted = job_details.get("extracted_details") or {}
    requirements = job_details.get("requirements_analysis") or {}
    company_analysis = state.get("company_analysis") or {}
    salary_analysis = state.get("salary_analysis") or {}

    company_name = extracted.get("company_name") or "the company"
    job_title = extracted.get("job_title") or "this position"

    job_details_str = "\n".join(
        [
            f"- **Company:** {extracted.get('company_name', 'Not specified')}",
            f"- **Position:** {extracted.get('job_title', 'Not specified')}",
            f"- **Location:** {extracted.get('location', 'Not specified')}",
            f"- **Experience Required:** {extracted.get('experience_level', 'Not specified')}",
            f"- **Job Type:** {extracted.get('job_type', 'Not specified')}",
            f"- **Compensation:** {extracted.get('compensation', 'Not specified')}",
        ]
    )

    prompt = f"""
Generate a comprehensive job analysis report from the data below.

## Job Details
{job_details_str}

## Requirements Analysis
{json.dumps(requirements, indent=2)}

## Company Analysis
{json.dumps(company_analysis, indent=2)}

## Salary Analysis
{json.dumps(salary_analysis, indent=2)}

Structure your markdown report with these sections:
1. **Executive Summary** — position, key findings, headline verdict.
2. **Company Profile** — stability, market position, culture signals. Repeat any
   data caveats from the inputs (e.g. "layoffs data not available", "salary
   figures are estimates"). Do NOT pretend you have data you weren't given.
3. **Job Requirements Analysis** — required skills/experience and how
   distinctive they are.
4. **Compensation Analysis** — range, COL context, negotiation moves.
5. **Final Recommendation** — exactly one of:
   `Highly Recommended` / `Recommended` / `Consider with Caution` / `Not Recommended`,
   followed by the top three reasons.

Rules:
- Do not output `<think>` blocks or markdown code fences around the whole report.
- Do not fabricate company news, layoffs, or salary benchmarks.
"""

    try:
        report = get_completion(prompt, model)
    except Exception as exc:  # noqa: BLE001 - surfaced into pipeline state
        state["error"] = f"Report generation failed: {exc}"
        return state

    if not report.lstrip().startswith("#"):
        report = f"# Job Analysis Report: {job_title} at {company_name}\n\n{report}"

    # Strip stray markdown fences the model might wrap the whole report in.
    if "```markdown" in report:
        report = report.replace("```markdown", "").replace("```", "")

    state["final_report"] = report

    if progress_callback:
        progress_callback("report", 100, "Final report generated successfully")

    return state
