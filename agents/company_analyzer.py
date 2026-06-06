from typing import Dict

from tools.company_tools import analyze_company_stability, analyze_culture_signals


def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state

    model = state.get("model", "detailed")
    progress_callback = state.get("progress_callback")

    if progress_callback:
        progress_callback("company", 50)

    # Prefer the user-supplied company name; fall back to extraction.
    manual_inputs = state.get("manual_inputs") or {}
    company_name = manual_inputs.get("company_name") if isinstance(manual_inputs, dict) else None

    if not company_name:
        extracted = (state.get("job_details") or {}).get("extracted_details") or {}
        if isinstance(extracted, dict):
            company_name = (
                extracted.get("company_name")
                or extracted.get("Company Name")
                or extracted.get("company")
            )

    if not company_name:
        state["error"] = "Company analysis failed: company name missing from inputs and extraction."
        return state

    try:
        stability = analyze_company_stability(company_name, model)
        culture = analyze_culture_signals(company_name, model)
        state["company_analysis"] = {
            "stability_analysis": stability,
            "culture_signals": culture,
        }
        if progress_callback:
            progress_callback("company", 100, f"Analyzed signals for {company_name}")
    except Exception as exc:  # noqa: BLE001 - surfaced into pipeline state
        state["error"] = f"Company analysis failed for {company_name}: {exc}"

    return state
