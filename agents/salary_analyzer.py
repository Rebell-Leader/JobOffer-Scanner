from tools.salary_tools import salary_tools
from typing import Dict

def analyze(state: Dict) -> Dict:
    if state.get("error"):
        return state

    # Get model and progress callback from state
    model = state.get("model", "deepseek-ai/DeepSeek-R1")
    progress_callback = state.get("progress_callback")

    # Call progress callback if available
    if progress_callback:
        progress_callback("salary", 75)

    try:
        job_details = state.get("job_details", {})
        extracted_details = job_details.get("extracted_details", {})

        # Print debug information to help diagnose issues
        print(f"Salary analysis - extracted_details: {extracted_details}")

        # Extract location and job title with better fallbacks
        job_title = extracted_details.get("job_title", "")
        location = extracted_details.get("location", "")
        experience_level = extracted_details.get("experience_level", "")

        # Get manual inputs if available
        manual_inputs = state.get("manual_inputs", {})
        if manual_inputs and isinstance(manual_inputs, dict):
            # Override with manual inputs if available
            if manual_inputs.get("job_title"):
                job_title = manual_inputs.get("job_title")
                print(f"Using manual job_title: {job_title}")

            if manual_inputs.get("location"):
                location = manual_inputs.get("location")
                print(f"Using manual location: {location}")

            if manual_inputs.get("experience_level"):
                experience_level = manual_inputs.get("experience_level")
                print(f"Using manual experience_level: {experience_level}")

        # Make sure we have at least minimal data
        if not job_title:
            job_title = "Software Engineer"  # Default fallback
            print("Using default job title: Software Engineer")

        if not location:
            location = "United States"  # Default fallback
            print("Using default location: United States")

        print(f"Salary analysis using - Title: {job_title}, Location: {location}, Experience: {experience_level}")

        # Call the salary estimation tool
        salary_range = salary_tools[0].func(
            job_title=job_title,
            location=location,
            experience_level=experience_level,
            model=model  # Pass the model explicitly
        )

        state["salary_analysis"] = {
            "estimated_range": salary_range
        }

        # Call progress callback with info
        if progress_callback:
            summary = f"Analyzed salary range for {job_title} in {location}"
            progress_callback("salary", 100, summary)

    except Exception as e:
        state["error"] = f"Salary analysis failed: {str(e)}"
        print(f"Salary analysis error: {str(e)}")

    return state