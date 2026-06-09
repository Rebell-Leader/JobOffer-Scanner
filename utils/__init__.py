"""Leaf-layer utilities (no imports from services/agents/tools/api/bot/worker).

Import submodules by their full path — ``from utils.cache import cache``,
``from utils.llm import get_completion`` — rather than re-exporting symbols
here, which would shadow the same-named submodules (e.g. ``from utils import
cache`` resolving to the value instead of the module).
"""
