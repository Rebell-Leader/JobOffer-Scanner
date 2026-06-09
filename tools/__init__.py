"""LLM-facing tools + ingestion.

Import submodules by their full path — ``from tools.job_tools import
extract_job_details`` — rather than re-exporting the ``*_tools`` Tool lists
here, which would shadow the same-named submodules (``from tools import
job_tools`` resolving to the list instead of the module).
"""
