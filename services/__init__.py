"""Business-logic services.

Importing this package wires up the LLM usage ledger: ``services.usage``
registers its recorder with ``utils.llm`` (observer pattern) so token
accounting works without ``utils.llm`` importing the services layer. Every
entry point imports some service, so this runs early enough to capture all
real completions.
"""

from services import usage as _usage  # noqa: F401  (import for its side effect)
