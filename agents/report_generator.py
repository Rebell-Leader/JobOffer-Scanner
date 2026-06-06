import json
from typing import Dict

from utils.llm import get_completion
from utils.verdict import extract_verdict, strip_verdict_block


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
    resume_analysis = state.get("resume_analysis") or {}

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

    resume_section = ""
    if resume_analysis:
        resume_section = f"""

## Resume / ATS Analysis (provided)
- ATS keyword match score (deterministic): **{resume_analysis.get('ats_score')}/100**
- Matched required skills: {resume_analysis.get('matched_skills', [])}
- Missing required skills: {resume_analysis.get('missing_skills', [])}
- ATS formatting issues: {resume_analysis.get('format_issues', [])}

Resume gap commentary:
{resume_analysis.get('commentary', '')}
"""

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
{resume_section}

Structure your markdown report with these sections:
1. **Executive Summary** — position, key findings, headline verdict.
2. **Company Profile** — stability, market position, culture signals. Repeat any
   data caveats from the inputs (e.g. "layoffs data not available", "salary
   figures are estimates"). Do NOT pretend you have data you weren't given.
3. **Job Requirements Analysis** — required skills/experience and how
   distinctive they are.
4. **Compensation Analysis** — range, COL context, negotiation moves.
5. **Resume Fit** (only if Resume / ATS Analysis was provided above) — lead
   with the deterministic ATS score, then summarize the gap commentary.
6. **Final Recommendation** — exactly one of:
   `Highly Recommended` / `Recommended` / `Consider with Caution` / `Not Recommended`,
   followed by the top three reasons.

After the markdown report, append a structured verdict block on its OWN line,
with no surrounding prose, in EXACTLY this format:

<verdict_json>
{{"verdict": "<one of the four labels>", "reasons": ["...", "...", "..."], "confidence": <integer 1-10>}}
</verdict_json>

Rules:
- Do not output `<think>` blocks or markdown code fences around the whole report.
- Do not fabricate company news, layoffs, or salary benchmarks.
- The verdict in the JSON block MUST match the one in the markdown.
"""

    try:
        report = get_completion(prompt, model)
    except Exception as exc:  # noqa: BLE001 - surfaced into pipeline state
        state["error"] = f"Report generation failed: {exc}"
        return state

    if not report.lstrip().startswith("#"):
        report = f"# Job Analysis Report: {job_title} at {company_name}\n\n{report}"

    if "```markdown" in report:
        report = report.replace("```markdown", "").replace("```", "")

    # Extract structured verdict, then strip the sidecar from user-facing markdown.
    state["verdict"] = extract_verdict(report)
    state["final_report"] = strip_verdict_block(report)

    if progress_callback:
        progress_callback("report", 100, "Final report generated successfully")

    return state
